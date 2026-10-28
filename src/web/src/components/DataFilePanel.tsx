// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { DataFileListing } from "../lib/types";

interface DataFilePanelProps {
  listing: DataFileListing | null;
  isLoading: boolean;
  errorMessage: string;
  onRefresh: () => void;
  onUpload: () => void;
  onEnterFolder: (prefix: string) => void;
  onNavigatePrefix: (prefix: string) => void;
  // Download is offered only in the read-only result areas (training/inference
  // output); the writable input areas offer delete instead.
  onDownload: (key: string, name: string) => void;
  onDelete: (key: string, name: string) => void;
  // Key of the file currently being deleted, to disable its row action.
  deletingKey: string;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// Build clickable breadcrumb segments from a prefix like "data/foo/bar/".
function breadcrumbs(prefix: string): Array<{ label: string; prefix: string }> {
  const parts = prefix.replace(/\/$/, "").split("/");
  const crumbs: Array<{ label: string; prefix: string }> = [];
  let acc = "";
  for (const part of parts) {
    acc += `${part}/`;
    crumbs.push({ label: part, prefix: acc });
  }
  return crumbs;
}

export function DataFilePanel({
  listing,
  isLoading,
  errorMessage,
  onRefresh,
  onUpload,
  onEnterFolder,
  onNavigatePrefix,
  onDownload,
  onDelete,
  deletingKey,
}: DataFilePanelProps) {
  const prefix = listing?.prefix ?? "data/";
  // The four browsable S3 areas. Output areas are read-only (no upload).
  const areas: ReadonlyArray<{
    root: string;
    label: string;
    uploadable: boolean;
    testId: string;
  }> = [
    { root: "data/", label: "训练数据", uploadable: true, testId: "data-area-training-data" },
    { root: "output/", label: "训练结果", uploadable: false, testId: "data-area-training-output" },
    { root: "inference_data/", label: "预测数据", uploadable: true, testId: "data-area-inference-data" },
    { root: "inference_output/", label: "预测结果", uploadable: false, testId: "data-area-inference-output" },
  ];
  const activeArea =
    areas.find((a) => prefix.startsWith(a.root))?.root ?? "data/";
  const canUpload = areas.find((a) => a.root === activeArea)?.uploadable ?? false;
  // Deletion is allowed only in the user-writable input areas (same as upload);
  // output areas hold artifacts and are read-only, so they only offer download.
  const canDelete = canUpload;
  return (
    <section className="content-panel" data-testid="data-file-view">
      <div className="content-header">
        <h2 className="content-title">数据管理</h2>
        <div className="content-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={onRefresh}
            data-testid="data-refresh-button"
          >
            刷新
          </button>
          {canUpload ? (
            <button
              className="primary-button"
              type="button"
              onClick={onUpload}
              data-testid="data-upload-button"
            >
              + 上传数据
            </button>
          ) : null}
        </div>
      </div>

      <div className="data-area-tabs" data-testid="data-area-tabs">
        {areas.map((area) => (
          <button
            key={area.root}
            type="button"
            className={activeArea === area.root ? "area-tab active" : "area-tab"}
            onClick={() => onNavigatePrefix(area.root)}
            data-testid={area.testId}
          >
            {area.label}
          </button>
        ))}
      </div>

      <div className="breadcrumbs" data-testid="data-breadcrumbs">
        {breadcrumbs(prefix).map((crumb, index) => (
          <span key={crumb.prefix}>
            {index > 0 ? <span className="crumb-sep">/</span> : null}
            <button
              type="button"
              className="crumb-link"
              onClick={() => onNavigatePrefix(crumb.prefix)}
            >
              {crumb.label}
            </button>
          </span>
        ))}
      </div>

      {errorMessage ? <div className="error-text">{errorMessage}</div> : null}

      {isLoading ? (
        <div className="muted-text">加载中...</div>
      ) : !listing || (listing.folders.length === 0 && listing.files.length === 0) ? (
        <div className="empty-state">
          {canUpload
            ? "该目录暂无文件，点击右上角「上传数据」。"
            : "暂无数据。此目录的文件由训练/推理任务自动产出。"}
        </div>
      ) : (
        <table className="data-table" data-testid="data-file-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>大小</th>
              <th>修改时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {listing.folders.map((folder) => (
              <tr
                key={folder}
                className="data-row"
                tabIndex={0}
                role="button"
                onClick={() => onEnterFolder(folder)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onEnterFolder(folder);
                  }
                }}
                data-testid={`data-folder-${folder}`}
              >
                <td>📁 {folder.replace(prefix, "").replace(/\/$/, "")}</td>
                <td>目录</td>
                <td>—</td>
                <td>—</td>
                <td>—</td>
              </tr>
            ))}
            {listing.files.map((entry) => (
              <tr key={entry.key} data-testid={`data-file-${entry.name}`}>
                <td>📄 {entry.name}</td>
                <td>文件</td>
                <td>{formatSize(entry.size)}</td>
                <td className="muted-text">{entry.last_modified ?? "—"}</td>
                <td className="data-row-actions">
                  {canDelete ? (
                    <button
                      type="button"
                      className="danger-button data-action-button"
                      onClick={() => onDelete(entry.key, entry.name)}
                      disabled={deletingKey === entry.key}
                      data-testid={`data-delete-${entry.name}`}
                    >
                      {deletingKey === entry.key ? "删除中..." : "删除"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="secondary-button data-action-button"
                      onClick={() => onDownload(entry.key, entry.name)}
                      data-testid={`data-download-${entry.name}`}
                    >
                      下载
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
