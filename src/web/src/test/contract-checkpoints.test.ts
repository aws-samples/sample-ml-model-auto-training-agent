// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../lib/api";
import { buildPreview } from "../lib/preview";
import confirmedSubmissionFixture from "../../../tests/contracts/fixtures/confirmed_task_submission.json";

const fixture = confirmedSubmissionFixture as {
  frontend_config: {
    apiBaseUrl: string;
    environmentLabel: string;
    submittedBy: string;
    signRegion: string;
    signHost: string;
    accessKeyId: string;
    secretAccessKey: string;
  };
  draft: {
    taskName: string;
    taskType: "timeseries_forecast";
    dataLocation: string;
    outputLocation: string;
    modelName: string;
    targetEndpoint: string;
    comment: string;
    imageTag: string;
    deploymentMode: "auto" | "manual";
    isRecurring: boolean;
    scheduleStart: string;
    intervalValue: string;
    intervalUnit: "minutes" | "hours" | "days";
  };
  payload: {
    task_name: string;
    task_type: "timeseries_forecast";
    comment: string;
    data_location: string;
    output_location: string;
    model_name: string;
    target_endpoint: string;
    submitted_by: string;
    image_tag: string;
    deployment_mode: "auto" | "manual";
    confirmation_flag: true;
  };
  success_response: {
    task_id: string;
    status: string;
    message: string;
  };
  backend_error: {
    status: number;
    message: string;
    error_code: string;
  };
};

function jsonResponse(payload: unknown, options?: { ok?: boolean; status?: number }): Response {
  return {
    ok: options?.ok ?? true,
    status: options?.status ?? 200,
    text: async () => JSON.stringify(payload),
  } as Response;
}

describe("contract checkpoints", () => {
  it("checkpoint 1 preview payload matches the shared confirmed submission fixture", () => {
    const preview = buildPreview(fixture.draft, fixture.frontend_config);

    expect(preview.payload).toEqual(fixture.payload);
  });

  it("checkpoint 1 api client preserves success and stable backend error codes", async () => {
    const client = new ApiClient(fixture.frontend_config);

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(fixture.success_response)));
    await expect(client.submitTask(fixture.payload)).resolves.toEqual(fixture.success_response);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          {
            message: fixture.backend_error.message,
            error_code: fixture.backend_error.error_code,
          },
          {
            ok: false,
            status: fixture.backend_error.status,
          },
        ),
      ),
    );

    await expect(client.submitTask(fixture.payload)).rejects.toEqual(
      expect.objectContaining({
        code: fixture.backend_error.error_code,
        message: `[${fixture.backend_error.error_code}] ${fixture.backend_error.message}`,
        status: fixture.backend_error.status,
      }),
    );
  });
});
