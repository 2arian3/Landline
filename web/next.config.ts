import type { NextConfig } from "next";

// The browser talks to /api/* on this origin; Next forwards it to the Python API.
// Set API_URL to the VM in production; locally it's `make dev` on :8000.
const API_URL = process.env.API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,
  // gzip buffers the proxied event stream (/api/sessions/:id/stream) until it closes.
  compress: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default config;
