// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { FrontendRuntimeConfiguration } from "./types";

declare global {
  interface Window {
    __APP_RUNTIME_CONFIG__?: Partial<FrontendRuntimeConfiguration>;
  }
}

export function loadRuntimeConfiguration(): FrontendRuntimeConfiguration {
  const runtime = window.__APP_RUNTIME_CONFIG__ ?? {};
  const str = (v: unknown): string => String(v ?? "").trim();
  const config: FrontendRuntimeConfiguration = {
    apiBaseUrl: str(runtime.apiBaseUrl),
    environmentLabel: str(runtime.environmentLabel) || "PoC",
    submittedBy: str(runtime.submittedBy) || "demo-operator",
  };
  // Populate only the auth fields that are present, so usesTokenAuth() below can
  // distinguish the two modes.
  const pocToken = str(runtime.pocToken);
  if (pocToken) config.pocToken = pocToken;
  const signRegion = str(runtime.signRegion);
  if (signRegion) config.signRegion = signRegion;
  const signHost = str(runtime.signHost);
  if (signHost) config.signHost = signHost;
  const accessKeyId = str(runtime.accessKeyId);
  if (accessKeyId) config.accessKeyId = accessKeyId;
  const secretAccessKey = str(runtime.secretAccessKey);
  if (secretAccessKey) config.secretAccessKey = secretAccessKey;
  const sessionToken = str(runtime.sessionToken);
  if (sessionToken) config.sessionToken = sessionToken;
  return config;
}

// Commercial-region mode: a pre-shared token is sent as a plain Authorization
// header. Otherwise SigV4 (China) mode is used.
export function usesTokenAuth(config: FrontendRuntimeConfiguration): boolean {
  return Boolean(config.pocToken);
}

export function validateRuntimeConfiguration(
  config: FrontendRuntimeConfiguration,
): string[] {
  const errors: string[] = [];
  if (!config.apiBaseUrl) {
    errors.push("缺少 API 地址配置");
  }
  if (!config.submittedBy) {
    errors.push("缺少提交人配置");
  }
  if (usesTokenAuth(config)) {
    // Token mode needs nothing else.
  } else if (config.signHost && config.signRegion) {
    if (!config.accessKeyId || !config.secretAccessKey) {
      errors.push("缺少 SigV4 凭证");
    }
  } else {
    errors.push("缺少鉴权配置（token 或 SigV4 签名信息）");
  }
  return errors;
}
