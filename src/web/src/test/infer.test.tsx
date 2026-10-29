// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";

function jsonResponse(payload: unknown, options?: { ok?: boolean; status?: number }): Response {
  return {
    ok: options?.ok ?? true,
    status: options?.status ?? 200,
    text: async () => JSON.stringify(payload),
  } as Response;
}

const INFER_TASK = {
  task_id: "infer-1",
  task_name: "推理任务甲",
  status: "RUNNING",
  task_type: "timeseries_forecast",
  model_name: "timeseries_model",
  target_endpoint: "timeseries-endpoint",
  data_location: "inference_data/predict.csv",
  output_location: "inference_output/infer-1",
  submitted_by: "demo-operator",
  description: "预测未来 7 天目标指标",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
};

describe("Inference task flow", () => {
  beforeEach(() => {
    sessionStorage.setItem(
      "demo-auth-user",
      JSON.stringify({ username: "admin", role: "admin", displayName: "admin" }),
    );
    window.__APP_RUNTIME_CONFIG__ = {
      apiBaseUrl: "/poc",
      environmentLabel: "PoC",
      submittedBy: "demo-operator",
      signRegion: "cn-northwest-1",
      signHost: "example.execute-api.cn-northwest-1.amazonaws.com.cn",
      accessKeyId: "AKIAEXAMPLE",
      secretAccessKey: "secretexample",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), "http://localhost").pathname;
        const method = init?.method ?? "GET";
        if (path.endsWith("/infer-tasks") && method === "POST") {
          return jsonResponse({
            task_id: "infer-9",
            status: "RUNNING",
            message: "ok",
          });
        }
        if (path.endsWith("/infer-tasks")) {
          return jsonResponse({ tasks: [INFER_TASK] });
        }
        if (path.endsWith("/models")) {
          return jsonResponse({
            models: [
              {
                model_record_id: "m-1",
                task_id: "t-1",
                model_name: "timeseries_model",
                target_endpoint: "timeseries-endpoint",
                status: "DEPLOYED",
                created_at: "2026-07-01T00:00:00Z",
              },
            ],
          });
        }
        if (path.endsWith("/tasks")) {
          return jsonResponse({ tasks: [] });
        }
        return jsonResponse({});
      }),
    );
  });

  it("renders the inference menu and renames the training menu", async () => {
    render(<App />);
    expect(screen.getByTestId("sidebar-menu-infer")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-menu-tasks")).toHaveTextContent("训练任务");
    expect(screen.getByTestId("sidebar-menu-infer")).toHaveTextContent("推理任务");
  });

  it("lists inference tasks and opens a detail view", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("sidebar-menu-infer"));
    expect(await screen.findByTestId("infer-task-list-item-infer-1")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("infer-task-list-item-infer-1"));
    expect(await screen.findByTestId("infer-task-detail-view")).toBeInTheDocument();
    expect(screen.getByTestId("infer-task-detail-info")).toHaveTextContent(
      "timeseries-endpoint",
    );
  });

  it("submits a new inference task through the wizard", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("sidebar-menu-infer"));
    fireEvent.click(await screen.findByTestId("new-infer-task-button"));
    expect(await screen.findByTestId("infer-task-wizard-modal")).toBeInTheDocument();
    // Pick a model from the registry dropdown (auto-fills the endpoint), then
    // submit.
    const modelSelect = await screen.findByTestId("infer-model-name-select");
    await waitFor(() =>
      expect(
        (modelSelect as HTMLSelectElement).querySelector(
          'option[value="timeseries_model"]',
        ),
      ).not.toBeNull(),
    );
    fireEvent.change(modelSelect, { target: { value: "timeseries_model" } });
    expect(screen.getByTestId("infer-target-endpoint-input")).toHaveValue(
      "timeseries-endpoint",
    );
    fireEvent.click(screen.getByTestId("infer-submit-button"));
    expect(await screen.findByTestId("infer-submit-success")).toHaveTextContent(
      "infer-9",
    );
  });
});
