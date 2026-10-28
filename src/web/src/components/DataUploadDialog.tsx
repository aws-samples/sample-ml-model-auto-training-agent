// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useRef, useState } from "react";

import { Modal } from "./Modal";
import type { ApiClient } from "../lib/api";

interface DataUploadDialogProps {
  open: boolean;
  apiClient: ApiClient;
  currentPrefix: string;
  existingFolders: string[];
  onClose: () => void;
  onUploaded: () => void;
}

// File carries webkitRelativePath when selected via a directory picker.
interface PickedFile {
  file: File;
  relativePath: string;
}

function joinSubdir(base: string, extra: string): string {
  const parts = [base, extra]
    .map((segment) => segment.replace(/^\/+|\/+$/g, ""))
    .filter(Boolean);
  return parts.join("/");
}

// Mirror of the backend KEY_SEGMENT_PATTERN (api/data_files.py): a path segment
// must start with an alphanumeric and otherwise contain only [A-Za-z0-9._-].
const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

// A file is uploadable only if its name and every directory segment of its
// relative path satisfy the backend rule. Dot-prefixed files (e.g. .DS_Store,
// .gitkeep) fail because the first character is not alphanumeric — the backend
// rejects them with [VALIDATION_ERROR], so we skip them client-side instead of
// letting one bad file abort the whole batch.
export function isUploadable(relativePath: string): boolean {
  const segments = relativePath.split("/").filter(Boolean);
  return segments.length > 0 && segments.every((seg) => SAFE_SEGMENT.test(seg));
}

export function DataUploadDialog({
  open,
  apiClient,
  currentPrefix,
  existingFolders,
  onClose,
  onUploaded,
}: DataUploadDialogProps) {
  const [folderMode, setFolderMode] = useState<"current" | "existing" | "new">("current");
  const [existingFolder, setExistingFolder] = useState("");
  const [newFolder, setNewFolder] = useState("");
  const [picked, setPicked] = useState<PickedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const dirInputRef = useRef<HTMLInputElement>(null);

  function resolveSubdirectory(): string {
    if (folderMode === "existing" && existingFolder) {
      return existingFolder.startsWith(currentPrefix)
        ? existingFolder.slice(currentPrefix.length).replace(/\/$/, "")
        : existingFolder.replace(/\/$/, "");
    }
    if (folderMode === "new") {
      return newFolder.trim().replace(/^\/+|\/+$/g, "");
    }
    return "";
  }

  function onPickFiles(fileList: FileList | null) {
    const files = Array.from(fileList ?? []).map((file) => ({
      file,
      // For a directory pick, webkitRelativePath looks like "folder/sub/a.csv".
      relativePath:
        (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
    }));
    setPicked(files);
  }

  async function handleUpload() {
    if (picked.length === 0 || uploading) {
      return;
    }
    try {
      setUploading(true);
      setError("");
      const baseSubdir = resolveSubdirectory();
      // Skip files the backend would reject (e.g. dot-prefixed like .DS_Store),
      // so one unsupported file doesn't abort the whole batch.
      const uploadable = picked.filter((p) => isUploadable(p.relativePath));
      const skipped = picked.filter((p) => !isUploadable(p.relativePath));
      for (let i = 0; i < uploadable.length; i += 1) {
        const { file, relativePath } = uploadable[i];
        setProgress(`上传中 ${i + 1}/${uploadable.length}：${relativePath}`);
        // Split the relative path into directory part + file name so the folder
        // structure is preserved under the chosen target directory.
        const segments = relativePath.split("/");
        const fileName = segments.pop() as string;
        const subdirectory = joinSubdir(baseSubdir, segments.join("/"));
        const contentType = file.type || "application/octet-stream";
        const presigned = await apiClient.createUploadUrl({
          prefix: currentPrefix,
          subdirectory,
          fileName,
          contentType,
        });
        await apiClient.uploadToPresignedUrl(presigned.upload_url, file, contentType);
      }

      const skippedNote =
        skipped.length > 0
          ? `已跳过 ${skipped.length} 个不支持的文件（文件名含不支持的字符，如以 . 开头）：${skipped
              .map((p) => p.relativePath)
              .join("、")}`
          : "";

      if (uploadable.length === 0) {
        // Everything was skipped — nothing uploaded, so keep the dialog open
        // and explain why instead of closing as if it succeeded.
        setProgress("");
        setError(skippedNote || "没有可上传的文件");
        return;
      }

      setPicked([]);
      setNewFolder("");
      setProgress("");
      onUploaded();
      if (skippedNote) {
        // Some files uploaded, some skipped: surface the skip notice once, then
        // close — the dialog should not linger after a successful upload.
        window.alert(skippedNote);
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  return (
    <Modal open={open} title="上传训练数据" onClose={onClose} testId="data-upload-dialog">
      <div className="field section">
        <span className="field-label">目标目录</span>
        <div className="radio-group">
          <label className="radio-row">
            <input
              type="radio"
              name="folder-mode"
              checked={folderMode === "current"}
              onChange={() => setFolderMode("current")}
            />
            <span>当前目录（{currentPrefix}）</span>
          </label>
          <label className="radio-row">
            <input
              type="radio"
              name="folder-mode"
              checked={folderMode === "existing"}
              onChange={() => setFolderMode("existing")}
              disabled={existingFolders.length === 0}
            />
            <span>已有子目录</span>
          </label>
          {folderMode === "existing" ? (
            <select
              className="inline-select"
              value={existingFolder}
              onChange={(event) => setExistingFolder(event.target.value)}
              data-testid="upload-existing-folder"
            >
              <option value="">请选择子目录</option>
              {existingFolders.map((folder) => (
                <option key={folder} value={folder}>
                  {folder}
                </option>
              ))}
            </select>
          ) : null}
          <label className="radio-row">
            <input
              type="radio"
              name="folder-mode"
              checked={folderMode === "new"}
              onChange={() => setFolderMode("new")}
            />
            <span>新建子目录</span>
          </label>
          {folderMode === "new" ? (
            <input
              className="inline-input"
              type="text"
              placeholder="子目录名称，如 batch-202606"
              value={newFolder}
              onChange={(event) => setNewFolder(event.target.value)}
              data-testid="upload-new-folder"
            />
          ) : null}
        </div>
      </div>

      <div className="field section">
        <span className="field-label">选择上传内容</span>
        <div className="upload-pickers">
          <label className="picker-button">
            选择文件
            <input
              type="file"
              multiple
              hidden
              onChange={(event) => onPickFiles(event.target.files)}
              data-testid="upload-file-input"
            />
          </label>
          <label className="picker-button">
            选择文件夹
            <input
              ref={dirInputRef}
              type="file"
              hidden
              // Directory selection is non-standard; set the attribute imperatively.
              {...{ webkitdirectory: "", directory: "" }}
              onChange={(event) => onPickFiles(event.target.files)}
              data-testid="upload-folder-input"
            />
          </label>
        </div>
        {picked.length > 0 ? (
          <span className="muted-text">
            已选择 {picked.length} 个文件
            {picked.length === 1 ? `：${picked[0].relativePath}` : ""}
          </span>
        ) : null}
      </div>

      {progress ? <div className="muted-text section">{progress}</div> : null}
      {error ? <div className="error-text section">{error}</div> : null}

      <div className="actions section">
        <button
          type="button"
          className="primary-button"
          disabled={picked.length === 0 || uploading}
          onClick={() => void handleUpload()}
          data-testid="upload-confirm-button"
        >
          {uploading ? "上传中..." : "上传"}
        </button>
        <button type="button" className="secondary-button" onClick={onClose} disabled={uploading}>
          取消
        </button>
      </div>
    </Modal>
  );
}
