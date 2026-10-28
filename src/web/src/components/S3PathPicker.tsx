// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useEffect, useState } from "react";

import { Modal } from "./Modal";
import type { ApiClient } from "../lib/api";
import type { DataFileEntry } from "../lib/types";

interface S3PathPickerProps {
  apiClient: ApiClient;
  // Fixed root prefix the picker is confined to, e.g. "data/", "output/".
  prefix: string;
  // Full path value (prefix + subpath) stored in the draft.
  value: string;
  onChange: (fullValue: string) => void;
  // "file":        pick an existing file only.
  // "file-or-dir": pick an existing file OR a folder (training/prediction input,
  //                where a folder is used as a data prefix).
  // "dir":         pick an existing folder OR type a new one (output/result).
  mode: "file" | "file-or-dir" | "dir";
  placeholder?: string;
  testId?: string;
}

function browseDirOf(prefix: string, value: string): string {
  // The directory to open the browser at: for a file value, its parent; for a
  // dir value, the value itself.
  const full = value.startsWith(prefix) ? value : prefix;
  const dir = full.endsWith("/") ? full : full.slice(0, full.lastIndexOf("/") + 1);
  return dir || prefix;
}

const MODAL_TITLES: Record<"file" | "file-or-dir" | "dir", string> = {
  file: "选择文件",
  "file-or-dir": "选择文件或目录",
  dir: "选择或新建目录",
};

export function S3PathPicker({
  apiClient,
  prefix,
  value,
  onChange,
  mode,
  placeholder,
  testId,
}: S3PathPickerProps) {
  const [open, setOpen] = useState(false);
  const subpath = value.startsWith(prefix) ? value.slice(prefix.length) : value;

  return (
    <div className="s3-path-picker" data-testid={testId}>
      <div className="prefixed-path-input">
        <span className="prefixed-path-addon">{prefix}</span>
        <input
          className="prefixed-path-field"
          value={subpath}
          onChange={(event) => onChange(prefix + event.target.value)}
          placeholder={placeholder}
          data-testid={testId ? `${testId}-input` : undefined}
        />
        <button
          type="button"
          className="primary-button s3-picker-toggle"
          onClick={() => setOpen(true)}
          data-testid={testId ? `${testId}-browse` : undefined}
        >
          更换
        </button>
      </div>

      {open ? (
        <S3PathPickerModal
          apiClient={apiClient}
          prefix={prefix}
          mode={mode}
          initialDir={browseDirOf(prefix, value)}
          initialValue={value}
          testId={testId}
          onCancel={() => setOpen(false)}
          onConfirm={(selected) => {
            onChange(selected);
            setOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}

interface S3PathPickerModalProps {
  apiClient: ApiClient;
  prefix: string;
  mode: "file" | "file-or-dir" | "dir";
  initialDir: string;
  initialValue: string;
  testId?: string;
  onCancel: () => void;
  onConfirm: (fullValue: string) => void;
}

function S3PathPickerModal({
  apiClient,
  prefix,
  mode,
  initialDir,
  initialValue,
  testId,
  onCancel,
  onConfirm,
}: S3PathPickerModalProps) {
  const [browsePrefix, setBrowsePrefix] = useState(initialDir);
  const [folders, setFolders] = useState<string[]>([]);
  const [files, setFiles] = useState<DataFileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // The editable value (for dir mode: type a new/existing dir; kept in sync as
  // the user browses so "确定" always reflects the field).
  const initialSub = initialValue.startsWith(prefix)
    ? initialValue.slice(prefix.length)
    : "";
  const [draftSub, setDraftSub] = useState(initialSub);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    apiClient
      .listDataFiles(browsePrefix)
      .then((listing) => {
        if (cancelled) return;
        setFolders(listing.folders);
        setFiles(listing.files);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "目录加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [browsePrefix, apiClient]);

  const relative = browsePrefix.slice(prefix.length).replace(/\/$/, "");
  const segments = relative ? relative.split("/") : [];
  const crumbs = [
    { label: prefix, prefix },
    ...segments.map((seg, index) => ({
      label: seg,
      prefix: prefix + segments.slice(0, index + 1).join("/") + "/",
    })),
  ];

  function enterFolder(folderPrefix: string) {
    setBrowsePrefix(folderPrefix);
    if (mode === "dir") {
      setDraftSub(folderPrefix.slice(prefix.length));
    }
  }

  return (
    <Modal
      open
      title={MODAL_TITLES[mode]}
      onClose={onCancel}
      dismissable
      testId={testId ? `${testId}-modal` : undefined}
    >
      <div className="s3-picker-breadcrumbs">
        {crumbs.map((crumb, index) => (
          <span key={crumb.prefix}>
            {index > 0 ? <span className="crumb-sep">/</span> : null}
            <button
              type="button"
              className="crumb-link"
              onClick={() => setBrowsePrefix(crumb.prefix)}
            >
              {crumb.label}
            </button>
          </span>
        ))}
      </div>

      {error ? <div className="error-text">{error}</div> : null}
      {loading ? (
        <div className="muted-text">加载中...</div>
      ) : folders.length === 0 && files.length === 0 ? (
        <div className="muted-text s3-picker-empty">该目录为空</div>
      ) : (
        <ul className="s3-picker-list" data-testid={testId ? `${testId}-list` : undefined}>
          {folders.map((folder) => (
            <li key={folder} className="s3-picker-folder-row">
              <button
                type="button"
                className="s3-picker-entry s3-picker-folder"
                onClick={() => enterFolder(folder)}
              >
                📁 {folder.slice(browsePrefix.length).replace(/\/$/, "")}
              </button>
              {mode === "file-or-dir" ? (
                <button
                  type="button"
                  className="primary-button s3-picker-select-folder"
                  onClick={() => onConfirm(folder)}
                  data-testid={testId ? `${testId}-select-folder` : undefined}
                >
                  选择
                </button>
              ) : null}
            </li>
          ))}
          {mode === "file" || mode === "file-or-dir"
            ? files.map((file) => (
                <li key={file.key}>
                  <button
                    type="button"
                    className="s3-picker-entry s3-picker-file"
                    onClick={() => onConfirm(file.key)}
                    data-testid={testId ? `${testId}-file` : undefined}
                  >
                    📄 {file.name}
                  </button>
                </li>
              ))
            : null}
        </ul>
      )}

      {mode === "dir" ? (
        <div className="s3-picker-dir-confirm">
          <div className="prefixed-path-input">
            <span className="prefixed-path-addon">{prefix}</span>
            <input
              className="prefixed-path-field"
              value={draftSub}
              onChange={(event) => setDraftSub(event.target.value)}
              placeholder="可编辑，支持填写不存在的目录"
              data-testid={testId ? `${testId}-modal-input` : undefined}
            />
          </div>
          <div className="actions">
            <button
              type="button"
              className="primary-button"
              onClick={() => onConfirm(prefix + draftSub)}
              data-testid={testId ? `${testId}-confirm` : undefined}
            >
              确定
            </button>
            <button type="button" className="secondary-button" onClick={onCancel}>
              取消
            </button>
          </div>
        </div>
      ) : (
        <div className="actions">
          <button type="button" className="secondary-button" onClick={onCancel}>
            取消
          </button>
        </div>
      )}
    </Modal>
  );
}
