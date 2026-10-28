// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useState } from "react";

import type { ApiClient } from "../lib/api";
import type { AuthenticatedUser, UserRole } from "../lib/types";

interface LoginPageProps {
  apiClient: ApiClient;
  onLoggedIn: (user: AuthenticatedUser) => void;
}

export function LoginPage({ apiClient, onLoggedIn }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password || submitting) {
      return;
    }
    try {
      setSubmitting(true);
      setError("");
      const result = await apiClient.login(username.trim(), password);
      onLoggedIn({
        username: result.username,
        role: (result.role as UserRole) === "admin" ? "admin" : "user",
        displayName: result.display_name,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-shell" data-testid="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1 className="login-title">智能体驱动的模型训练平台</h1>
        <p className="muted-text">请登录以继续</p>
        <label className="field section">
          <span>用户名</span>
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            data-testid="login-username-input"
          />
        </label>
        <label className="field section">
          <span>密码</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            data-testid="login-password-input"
          />
        </label>
        {error ? (
          <div className="error-text section" data-testid="login-error">
            {error}
          </div>
        ) : null}
        <div className="actions section">
          <button
            type="submit"
            className="primary-button"
            disabled={!username.trim() || !password || submitting}
            data-testid="login-submit-button"
          >
            {submitting ? "登录中..." : "登录"}
          </button>
        </div>
      </form>
    </div>
  );
}
