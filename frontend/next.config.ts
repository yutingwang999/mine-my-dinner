import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // The backend accepts up to 5 screenshots of 10 MB each, so proxied uploads
  // must be allowed past Next's default 10 MB body limit.
  experimental: { proxyClientMaxBodySize: "52mb" },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default withNextIntl(nextConfig);
