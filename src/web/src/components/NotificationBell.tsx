// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useEffect, useState } from "react";

import { Modal } from "./Modal";
import type { ApiClient } from "../lib/api";
import type { NotificationMessage } from "../lib/types";

interface NotificationBellProps {
  apiClient: ApiClient;
  recipient: string;
}

function BellIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 3a6 6 0 0 0-6 6v3.5l-1.5 2.5h15L18 12.5V9a6 6 0 0 0-6-6Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M9.5 18a2.5 2.5 0 0 0 5 0"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function formatTime(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

export function NotificationBell({ apiClient, recipient }: NotificationBellProps) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<NotificationMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [markingId, setMarkingId] = useState("");

  async function loadMessages() {
    try {
      setLoading(true);
      setError("");
      setMessages(await apiClient.getMessages(recipient));
    } catch (err) {
      setError(err instanceof Error ? err.message : "消息加载失败");
    } finally {
      setLoading(false);
    }
  }

  // Prefetch unread messages on mount (and when the recipient changes) so the
  // badge shows the unread count immediately, without waiting for the user to
  // open the panel. This is a silent fetch: it does not toggle the loading
  // skeleton and swallows errors (the badge simply stays hidden on failure).
  useEffect(() => {
    let cancelled = false;
    apiClient
      .getMessages(recipient)
      .then((items) => {
        if (!cancelled) setMessages(items);
      })
      .catch(() => {
        /* silent: badge stays hidden until the user opens the panel */
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, recipient]);

  function openPanel() {
    setOpen(true);
    void loadMessages();
  }

  async function handleMarkRead(messageId: string) {
    if (markingId) return;
    try {
      setMarkingId(messageId);
      setError("");
      await apiClient.markMessageRead(messageId);
      // Only unread messages are shown, so drop it from the list immediately.
      setMessages((prev) => prev.filter((m) => m.message_id !== messageId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "标记已读失败");
    } finally {
      setMarkingId("");
    }
  }

  return (
    <>
      <button
        type="button"
        className="notification-bell-button"
        onClick={openPanel}
        aria-label="提醒"
        data-testid="notification-bell-button"
      >
        <BellIcon />
        {messages.length > 0 ? (
          <span className="notification-badge" data-testid="notification-badge">
            {messages.length}
          </span>
        ) : null}
      </button>

      <Modal
        open={open}
        title="最近消息"
        onClose={() => setOpen(false)}
        testId="notification-modal"
        dismissable
      >
        {error ? <div className="error-text section">{error}</div> : null}
        {loading ? (
          <div className="muted-text section">加载中...</div>
        ) : messages.length === 0 ? (
          <div className="empty-state">暂无未读消息</div>
        ) : (
          <ul className="notification-list">
            {messages.map((message) => (
              <li
                key={message.message_id}
                className="notification-item"
                data-testid="notification-item"
              >
                <div className="notification-item-main">
                  <div className="notification-content">{message.content}</div>
                  <div className="notification-meta muted-text">
                    {formatTime(message.created_at)}
                    {message.submitted_by ? ` · ${message.submitted_by}` : ""}
                  </div>
                </div>
                <button
                  type="button"
                  className="secondary-button notification-read-button"
                  onClick={() => void handleMarkRead(message.message_id)}
                  disabled={markingId === message.message_id}
                  data-testid="notification-read-button"
                >
                  {markingId === message.message_id ? "处理中..." : "已读"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Modal>
    </>
  );
}
