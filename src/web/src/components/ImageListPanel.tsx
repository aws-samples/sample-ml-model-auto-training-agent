// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { ImageEntry, ImageListing } from "../lib/types";

interface ImageListPanelProps {
  listing: ImageListing | null;
  isLoading: boolean;
  errorMessage: string;
  onRefresh: () => void;
  onNewImage: () => void;
  onSelect: (tag: string) => void;
}

function formatSize(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function tagLabel(entry: ImageEntry): string {
  return entry.tag || "(未命名)";
}

export function ImageListPanel({
  listing,
  isLoading,
  errorMessage,
  onRefresh,
  onNewImage,
  onSelect,
}: ImageListPanelProps) {
  return (
    <section className="content-panel" data-testid="image-list-view">
      <div className="content-header">
        <h2 className="content-title">训练Agent镜像</h2>
        <div className="content-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={onRefresh}
            data-testid="image-refresh-button"
          >
            刷新
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={onNewImage}
            data-testid="new-image-button"
          >
            + 新建训练Agent镜像
          </button>
        </div>
      </div>

      {errorMessage ? <div className="error-text">{errorMessage}</div> : null}

      {isLoading ? (
        <div className="muted-text">镜像列表加载中...</div>
      ) : !listing || listing.images.length === 0 ? (
        <div className="empty-state">
          暂无镜像，点击右上角「新建训练Agent镜像」。
        </div>
      ) : (
        <table className="data-table" data-testid="image-list-table">
          <thead>
            <tr>
              <th>标签</th>
              <th>大小</th>
              <th>状态</th>
              <th>创建时间</th>
              <th>更新时间</th>
            </tr>
          </thead>
          <tbody>
            {listing.images.map((entry) => (
              <tr
                key={entry.tag}
                className="data-row"
                tabIndex={0}
                role="button"
                onClick={() => onSelect(entry.tag)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(entry.tag);
                  }
                }}
                data-testid={`image-row-${entry.tag}`}
              >
                <td>
                  <strong>{tagLabel(entry)}</strong>
                </td>
                <td>{formatSize(entry.size)}</td>
                <td>
                  <span className="status-badge">{entry.status || "—"}</span>
                </td>
                <td className="muted-text">{entry.created_at || "—"}</td>
                <td className="muted-text">{entry.updated_at || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
