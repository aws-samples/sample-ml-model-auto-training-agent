// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { describe, expect, it } from "vitest";

import { isUploadable } from "../components/DataUploadDialog";

// 文件名校验需与后端 KEY_SEGMENT_PATTERN (api/data_files.py) 完全一致：
// 每段路径必须以字母数字开头，其余字符仅限 [A-Za-z0-9._-]。
describe("isUploadable", () => {
  it("accepts normal data files", () => {
    expect(isUploadable("train.csv")).toBe(true);
    expect(isUploadable("batch-202606/data_01.csv")).toBe(true);
    expect(isUploadable("a/b/c.parquet")).toBe(true);
  });

  it("skips dot-prefixed files the backend would reject", () => {
    // 这些会触发 [VALIDATION_ERROR] File name contains unsupported characters。
    expect(isUploadable(".DS_Store")).toBe(false);
    expect(isUploadable(".gitkeep")).toBe(false);
    // 隐藏文件出现在子目录里同样跳过。
    expect(isUploadable("batch-202606/.DS_Store")).toBe(false);
    // 子目录段以 . 开头也不行。
    expect(isUploadable(".hidden/data.csv")).toBe(false);
  });

  it("rejects empty paths", () => {
    expect(isUploadable("")).toBe(false);
    expect(isUploadable("/")).toBe(false);
  });
});
