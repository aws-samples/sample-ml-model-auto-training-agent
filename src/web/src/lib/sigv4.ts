// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Minimal AWS Signature Version 4 *query-string* (presigned URL) signer for the
// browser, implemented with the WebCrypto API (no third-party dependency).
//
// Why query-string instead of header signing: AWS China API Gateway blocks
// public execute-api requests that use a Lambda authorizer or no authorizer
// (these require an ICP filing) with 403 at the edge. IAM-authorized
// (SigV4-signed) requests are not gated, so the frontend signs every call.
//
// But the frontend reaches the API through the same CloudFront distribution
// (same-origin, to avoid an unauthenticated CORS preflight). China-region
// legacy CloudFront REJECTS x-amz-* headers from the forwarded-header allowlist
// ("The parameter Header Name with value x-amz-date is not allowed"), so the
// header form of SigV4 (which must forward x-amz-date) is impossible. The
// query-string form puts ALL signing material in the query string and signs
// only the host header, so CloudFront just needs to forward the query string —
// no x-amz-* headers at all.
//
// CloudFront host pitfall: we sign against the API Gateway ORIGIN domain
// (signHost). CloudFront forwards to that origin with Host = origin domain
// (Host is not in the forwarded set), so the Host API Gateway validates matches
// what we signed.

export interface SigV4Credentials {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken?: string;
}

export interface SigV4PresignInput {
  method: string;
  // Absolute URL the browser actually fetches (CloudFront same-origin URL),
  // including any query string the request already carries.
  url: string;
  // The host to sign against (the API Gateway origin domain, NOT CloudFront).
  signHost: string;
  region: string;
  service: string; // "execute-api"
  // Request body, if any. For execute-api query signing the payload hash in the
  // canonical request is the SHA256 of the body (empty-string hash for GET),
  // matching botocore's SigV4QueryAuth — NOT the literal "UNSIGNED-PAYLOAD".
  body?: string;
  expiresSeconds?: number;
}

const encoder = new TextEncoder();

function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256Hex(data: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(data));
  return toHex(digest);
}

async function hmac(
  key: ArrayBuffer | Uint8Array,
  data: string,
): Promise<ArrayBuffer> {
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    key as ArrayBuffer,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return crypto.subtle.sign("HMAC", cryptoKey, encoder.encode(data));
}

// amzDate: YYYYMMDDTHHMMSSZ, dateStamp: YYYYMMDD
function buildDateStamps(now: Date): { amzDate: string; dateStamp: string } {
  const amzDate = now.toISOString().replace(/[:-]|\.\d{3}/g, "");
  const dateStamp = amzDate.slice(0, 8);
  return { amzDate, dateStamp };
}

function encodeRfc3986(str: string): string {
  return encodeURIComponent(str).replace(
    /[!'()*]/g,
    (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase(),
  );
}

// Build a sorted, RFC3986-encoded canonical query string from name/value pairs.
function canonicalizeQuery(pairs: [string, string][]): string {
  const sorted = [...pairs].sort((a, b) =>
    a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0,
  );
  return sorted
    .map(([k, v]) => `${encodeRfc3986(k)}=${encodeRfc3986(v)}`)
    .join("&");
}

/**
 * Presign a request: return the same URL with SigV4 query parameters appended.
 * The returned URL is fetched as-is (GET/POST) with NO auth headers — all
 * signing material lives in the query string and only `host` is signed.
 */
export async function presignUrl(
  input: SigV4PresignInput,
  creds: SigV4Credentials,
  now: Date = new Date(),
): Promise<string> {
  const { amzDate, dateStamp } = buildDateStamps(now);
  const url = new URL(input.url);
  const canonicalUri = url.pathname || "/";
  const scope = `${dateStamp}/${input.region}/${input.service}/aws4_request`;
  const expires = String(input.expiresSeconds ?? 300);

  // Start from the request's existing query params, then add the X-Amz-* set.
  const pairs: [string, string][] = [];
  url.searchParams.forEach((value, key) => {
    pairs.push([key, value]);
  });
  pairs.push(["X-Amz-Algorithm", "AWS4-HMAC-SHA256"]);
  pairs.push(["X-Amz-Credential", `${creds.accessKeyId}/${scope}`]);
  pairs.push(["X-Amz-Date", amzDate]);
  pairs.push(["X-Amz-Expires", expires]);
  if (creds.sessionToken) {
    pairs.push(["X-Amz-Security-Token", creds.sessionToken]);
  }
  pairs.push(["X-Amz-SignedHeaders", "host"]);

  const canonicalQs = canonicalizeQuery(pairs);
  const canonicalHeaders = `host:${input.signHost.toLowerCase()}\n`;
  const signedHeaders = "host";
  // execute-api query signing hashes the actual payload (empty-string hash for
  // GET), matching botocore's SigV4QueryAuth — not "UNSIGNED-PAYLOAD".
  const payloadHash = await sha256Hex(input.body ?? "");

  const canonicalRequest = [
    input.method.toUpperCase(),
    canonicalUri,
    canonicalQs,
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");

  const stringToSign = [
    "AWS4-HMAC-SHA256",
    amzDate,
    scope,
    await sha256Hex(canonicalRequest),
  ].join("\n");

  const kDate = await hmac(
    encoder.encode(`AWS4${creds.secretAccessKey}`),
    dateStamp,
  );
  const kRegion = await hmac(kDate, input.region);
  const kService = await hmac(kRegion, input.service);
  const kSigning = await hmac(kService, "aws4_request");
  const signature = toHex(await hmac(kSigning, stringToSign));

  return `${url.origin}${canonicalUri}?${canonicalQs}&X-Amz-Signature=${signature}`;
}
