// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { FrontendRuntimeConfiguration } from "./types";
import { presignUrl } from "./sigv4";

/**
 * Presign an API request URL with SigV4 query-string auth.
 *
 * AWS China API Gateway only serves IAM-authorized (SigV4-signed) public
 * requests; a Lambda authorizer / unauthenticated call is blocked at the edge
 * without an ICP filing. The query-string (presigned) form is used because the
 * request goes through China-region legacy CloudFront, which refuses to forward
 * x-amz-* headers — so the header form of SigV4 is impossible. All signing
 * material lives in the query string here.
 *
 * `absoluteUrl` is the same-origin URL the browser fetches (CloudFront), while
 * the signature's Host is config.signHost (the API Gateway origin domain) —
 * CloudFront forwards to that origin with the matching Host.
 */
export async function presignApiUrl(
  config: FrontendRuntimeConfiguration,
  params: { method: string; absoluteUrl: string; body?: string },
): Promise<string> {
  return presignUrl(
    {
      method: params.method,
      url: params.absoluteUrl,
      signHost: config.signHost ?? "",
      region: config.signRegion ?? "",
      service: "execute-api",
      body: params.body,
    },
    {
      accessKeyId: config.accessKeyId ?? "",
      secretAccessKey: config.secretAccessKey ?? "",
      sessionToken: config.sessionToken,
    },
  );
}

/**
 * Commercial-region auth: the pre-shared token is sent verbatim as the
 * Authorization header, which the Lambda TokenAuthorizer validates.
 */
export function buildTokenHeaders(
  config: FrontendRuntimeConfiguration,
  hasBody: boolean,
): Record<string, string> {
  const headers: Record<string, string> = {};
  if (config.pocToken) {
    headers.Authorization = config.pocToken;
  }
  if (hasBody) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

export function resolveSubmittedBy(
  config: FrontendRuntimeConfiguration,
): string {
  return config.submittedBy;
}
