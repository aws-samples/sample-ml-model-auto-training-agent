import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";

function jsonResponse(payload: unknown, options?: { ok?: boolean; status?: number }): Response {
  return {
    ok: options?.ok ?? true,
    status: options?.status ?? 200,
    text: async () => JSON.stringify(payload),
  } as Response;
}

const CONFIG = {
  apiBaseUrl: "/poc",
  environmentLabel: "PoC",
  submittedBy: "demo-operator",
  signRegion: "cn-northwest-1",
  signHost: "example.execute-api.cn-northwest-1.amazonaws.com.cn",
  accessKeyId: "AKIAEXAMPLE",
  secretAccessKey: "secretexample",
};

function stubLoginAs(role: "admin" | "user") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path.endsWith("/login")) {
        return jsonResponse({ username: role === "admin" ? "admin" : "user1", role, display_name: role });
      }
      return jsonResponse({ tasks: [] });
    }),
  );
}

describe("login + role gating", () => {
  beforeEach(() => {
    sessionStorage.clear();
    window.__APP_RUNTIME_CONFIG__ = CONFIG;
  });
  afterEach(() => {
    sessionStorage.clear();
  });

  it("shows the login page when not authenticated", () => {
    stubLoginAs("admin");
    render(<App />);
    expect(screen.getByTestId("login-page")).toBeInTheDocument();
    expect(screen.queryByTestId("sidebar")).not.toBeInTheDocument();
  });

  it("admin sees the training-agent image menu after login", async () => {
    stubLoginAs("admin");
    render(<App />);
    fireEvent.change(screen.getByTestId("login-username-input"), {
      target: { value: "admin" },
    });
    fireEvent.change(screen.getByTestId("login-password-input"), {
      target: { value: "example-admin-pw" },
    });
    fireEvent.click(screen.getByTestId("login-submit-button"));
    expect(await screen.findByTestId("sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-menu-images")).toBeInTheDocument();
  });

  it("regular user does NOT see the training-agent image menu", async () => {
    stubLoginAs("user");
    render(<App />);
    fireEvent.change(screen.getByTestId("login-username-input"), {
      target: { value: "user1" },
    });
    fireEvent.change(screen.getByTestId("login-password-input"), {
      target: { value: "example-user-pw" },
    });
    fireEvent.click(screen.getByTestId("login-submit-button"));
    expect(await screen.findByTestId("sidebar")).toBeInTheDocument();
    expect(screen.queryByTestId("sidebar-menu-images")).not.toBeInTheDocument();
    // but still sees the normal menus
    expect(screen.getByTestId("sidebar-menu-tasks")).toBeInTheDocument();
  });
});
