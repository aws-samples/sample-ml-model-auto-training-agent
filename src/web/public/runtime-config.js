// 本地开发（中国区 cn-northwest-1）配置：SigV4（IAM）鉴权 + Vite 代理模式。
// - apiBaseUrl 用相对路径 /poc：浏览器同源请求 localhost:5173/poc/*（无 CORS 预检），
//   由 Vite dev server 代理转发到中国区 API Gateway（见 vite.config.ts）。
// - changeOrigin 会把 Host 改写为 signHost 对应的 execute-api 域名，SigV4 签名保持有效。
// - 无 pocToken：前端检测到 signHost + accessKeyId 即走 SigV4 模式。
// 注意：本文件仅用于本地开发，请勿把填入静态密钥的版本提交到 git。
//       生产商业区应改回 token 模式（apiBaseUrl 填完整地址 + pocToken）。
window.__APP_RUNTIME_CONFIG__ = {
  apiBaseUrl: "/poc",
  environmentLabel: "PoC",
  submittedBy: "demo-operator",
  signHost: "xxx.execute-api.cn-northwest-1.amazonaws.com.cn",
  signRegion: "cn-northwest-1",
  accessKeyId: "",
  secretAccessKey: ""
};
