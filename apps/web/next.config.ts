import path from "node:path";
import { fileURLToPath } from "node:url";

import type { NextConfig } from "next";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  // A self-contained server in .next/standalone, for the Docker image.
  output: "standalone",
  outputFileTracingRoot: root,
  transpilePackages: ["@anvesha/api-client"],
  // Gzip would buffer Server-Sent Events (live run activity). Put compression in a
  // reverse proxy in front of the app if you want it.
  compress: false,
  poweredByHeader: false,
  turbopack: { root },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
