// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local-dev API origin. In production the frontend reaches the API same-origin
// via CloudFront (/poc/* behavior). Locally that CloudFront path is unavailable
// (China requires an ICP-filed custom domain), so the dev server proxies /poc
// straight to the API Gateway origin instead. Override with VITE_API_ORIGIN.
const API_ORIGIN =
  process.env.VITE_API_ORIGIN ??
  "https://example456.execute-api.cn-northwest-1.amazonaws.com.cn";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Same-origin in the browser: requests go to http://localhost:5173/poc/*
      // (so no CORS preflight). Vite forwards them to the API Gateway origin.
      // changeOrigin rewrites the Host header to the origin domain — which is
      // exactly what the frontend signed as signHost, so the SigV4 signature
      // stays valid. The query string (where all signing material lives) is
      // preserved by the proxy.
      "/poc": {
        target: API_ORIGIN,
        changeOrigin: true,
        secure: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});
