// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { ApiClient } from "../lib/api";
import type { AuthenticatedUser } from "../lib/types";
import { NotificationBell } from "./NotificationBell";

interface HeaderBarProps {
  environmentLabel: string;
  user?: AuthenticatedUser | null;
  apiClient?: ApiClient | null;
  onLogout?: () => void;
}

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员",
  user: "普通用户",
};

export function HeaderBar({
  environmentLabel,
  user,
  apiClient,
  onLogout,
}: HeaderBarProps) {
  return (
    <header className="header-bar">
      <div className="header-title">智能体驱动的模型训练平台</div>
      <div className="header-right">
        <div
          className="header-env"
          data-testid="header-environment-label"
        >
          {environmentLabel}
        </div>
        {user && apiClient ? (
          <NotificationBell apiClient={apiClient} recipient={user.username} />
        ) : null}
        {user ? (
          <div className="header-user" data-testid="header-user">
            <span className="muted-text">
              {user.displayName}（{ROLE_LABELS[user.role] ?? user.role}）
            </span>
            <button
              type="button"
              className="secondary-button"
              onClick={onLogout}
              data-testid="header-logout-button"
            >
              退出登录
            </button>
          </div>
        ) : null}
      </div>
    </header>
  );
}
