import type { NextConfig } from "next";

const API_PROXY = process.env.API_PROXY_TARGET || "http://127.0.0.1:8515";

const nextConfig: NextConfig = {
  // Hide Next.js floating badge on phone preview
  devIndicators: {
    appIsrStatus: false,
  },
  async rewrites() {
    return {
      beforeFiles: [],
      // Prefer filesystem Route Handlers (SSE proxy) before generic rewrite
      afterFiles: [{ source: "/backend/health", destination: `${API_PROXY}/health` }],
      fallback: [
        {
          source: "/backend/api/:path*",
          destination: `${API_PROXY}/api/:path*`,
        },
      ],
    };
  },
};

export default nextConfig;
