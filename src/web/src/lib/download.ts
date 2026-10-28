// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Trigger a browser download from base64 content returned inline by the API.
//
// In China we cannot hand the browser a presigned S3 URL: the S3 domain is
// 未备案 and the edge blocks direct browser access (returns UnauthorizedAccess).
// So result files are proxied through the signed execute-api as base64, and we
// reconstruct a Blob here to save the file — no S3 domain is ever hit.
export function downloadBase64(
  base64: string,
  fileName: string,
  contentType = "application/octet-stream",
): void {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  saveBlob(new Blob([bytes], { type: contentType }), fileName);
}

// Save arbitrary text (e.g. an evaluation report JSON already loaded in the UI)
// as a downloaded file. Handles non-ASCII (Chinese) correctly via a UTF-8 Blob.
export function downloadText(
  text: string,
  fileName: string,
  contentType = "application/json",
): void {
  saveBlob(new Blob([text], { type: contentType }), fileName);
}

function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = fileName;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
