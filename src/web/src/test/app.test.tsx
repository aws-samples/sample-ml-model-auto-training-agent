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

const TASK = {
  task_id: "task-1",
  status: "RUNNING",
  task_type: "timeseries_forecast",
  model_name: "demo-model",
  target_endpoint: "demo-endpoint",
  submitted_by: "demo-operator",
  resolved_image_tag: "stable",
  created_at: "2026-06-18T03:00:00Z",
  updated_at: "2026-06-18T03:00:00Z",
};

const MODEL = {
  model_record_id: "model-1",
  task_id: "task-1",
  model_name: "demo-model",
  target_endpoint: "demo-endpoint",
  resolved_image_tag: "stable",
  status: "DEPLOYED",
  provider_status: "InService",
  created_at: "2026-06-18T04:00:00Z",
  updated_at: "2026-06-18T04:00:00Z",
};

describe("App navigation shell", () => {
  beforeEach(() => {
    // Start already logged in as admin so the app renders past the login gate
    // and all menus (including admin-only 训练Agent镜像) are visible.
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
        // Requests are SigV4 query-presigned, so the URL carries an X-Amz-*
        // query string; match on the pathname only, not the full URL.
        const path = new URL(String(input), "http://localhost").pathname;
        const method = init?.method ?? "GET";
        if (path.endsWith("/tasks") && method === "POST") {
          return jsonResponse({ task_id: "task-9", status: "SUBMITTED", message: "ok" });
        }
        if (path.endsWith("/tasks")) {
          return jsonResponse({ tasks: [TASK] });
        }
        if (path.endsWith("/models")) {
          return jsonResponse({ models: [MODEL] });
        }
        if (path.endsWith("/logs")) {
          return jsonResponse({
            task_id: "task-1",
            status: "RUNNING",
            logs: [
              {
                timestamp: "2026-06-18T03:01:00Z",
                level: "INFO",
                event_type: "attempt-verified",
                attempt_number: 1,
                message: "独立核验通过",
              },
            ],
          });
        }
        return jsonResponse({});
      }),
    );
  });

  it("renders sidebar menus and the task list by default", async () => {
    render(<App />);
    expect(screen.getByTestId("sidebar-menu-tasks")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-menu-models")).toBeInTheDocument();
    expect(await screen.findByTestId("task-list-item-task-1")).toBeInTheDocument();
  });

  it("opens the new-task wizard modal and walks the three steps", async () => {
    render(<App />);
    fireEvent.click(await screen.findByTestId("new-task-button"));
    expect(await screen.findByTestId("task-wizard-modal")).toBeInTheDocument();

    // Step 1 -> 2. The comment is optional (defaults valid), but set it to
    // exercise the field.
    fireEvent.change(await screen.findByTestId("submission-comment-textarea"), {
      target: { value: "预测未来 7 天目标指标" },
    });
    fireEvent.click(screen.getByTestId("wizard-next-button"));

    // Step 2: confirm gate then submit
    expect(await screen.findByTestId("training-plan-preview-panel")).toBeInTheDocument();
    expect(screen.getByTestId("training-plan-confirm-button")).toBeDisabled();
    fireEvent.click(screen.getByTestId("training-plan-confirm-checkbox"));
    fireEvent.click(screen.getByTestId("training-plan-confirm-button"));

    // Step 3
    expect(await screen.findByTestId("wizard-finish-button")).toBeInTheDocument();
  });

  it("opens a task detail view with info and log tabs", async () => {
    render(<App />);
    fireEvent.click(await screen.findByTestId("task-list-item-task-1"));
    expect(await screen.findByTestId("task-detail-view")).toBeInTheDocument();
    expect(screen.getByTestId("task-detail-info")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("task-detail-tab-logs"));
    expect(await screen.findByText(/attempt-verified/)).toBeInTheDocument();
    expect(screen.getByText(/独立核验通过/)).toBeInTheDocument();
  });

  it("switches to models menu, lists models, and opens model detail", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("sidebar-menu-models"));
    expect(await screen.findByTestId("model-list-item-model-1")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("model-list-item-model-1"));
    expect(await screen.findByTestId("model-detail-view")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("model-detail-info")).toBeInTheDocument(),
    );
  });
});
