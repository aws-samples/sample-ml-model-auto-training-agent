// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../lib/api";

function textResponse(body: string, options?: { ok?: boolean; status?: number }): Response {
  return {
    ok: options?.ok ?? true,
    status: options?.status ?? 200,
    text: async () => body,
  } as Response;
}

const client = new ApiClient({
  apiBaseUrl: "/poc",
  environmentLabel: "PoC",
  submittedBy: "demo-operator",
  signRegion: "cn-northwest-1",
  signHost: "example.execute-api.cn-northwest-1.amazonaws.com.cn",
  accessKeyId: "AKIAEXAMPLE",
  secretAccessKey: "secretexample",
});

describe("ApiClient", () => {
  it("preserves backend error codes in thrown errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        textResponse(
          JSON.stringify({
            message: "并发任务数已达上限",
            error_code: "CONCURRENCY_LIMIT_EXCEEDED",
          }),
          { ok: false, status: 409 },
        ),
      ),
    );

    await expect(
      client.submitTask({
        task_name: "训练任务",
        task_type: "timeseries_forecast",
        comment: "训练备注",
        data_location: "data/train.csv",
        output_location: "output/run-1",
        model_name: "demo_model_1",
        target_endpoint: "demo-endpoint-1",
        submitted_by: "demo-operator",
        confirmation_flag: true,
      }),
    ).rejects.toEqual(
      expect.objectContaining({
        code: "CONCURRENCY_LIMIT_EXCEEDED",
        message: "[CONCURRENCY_LIMIT_EXCEEDED] 并发任务数已达上限",
      }),
    );
  });

  it("falls back to plain-text gateway errors when the payload is not json", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => textResponse("upstream gateway timeout", { ok: false, status: 504 })),
    );

    await expect(client.getTasks()).rejects.toEqual(
      expect.objectContaining({
        message: "upstream gateway timeout",
        status: 504,
      }),
    );
  });

  it("uses a plain Authorization token header in commercial (token) mode", async () => {
    const tokenClient = new ApiClient({
      apiBaseUrl: "https://abc.execute-api.us-west-2.amazonaws.com/poc",
      environmentLabel: "PoC",
      submittedBy: "demo-operator",
      pocToken: "secret-token-123",
    });
    const fetchMock = vi.fn(async () => textResponse(JSON.stringify({ tasks: [] })));
    vi.stubGlobal("fetch", fetchMock);

    await tokenClient.getTasks();

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    // Full API URL, NOT a presigned URL with X-Amz-* query params.
    expect(url).toBe("https://abc.execute-api.us-west-2.amazonaws.com/poc/tasks");
    expect(url).not.toContain("X-Amz-Signature");
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "secret-token-123",
    );
  });
});
