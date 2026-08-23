import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies to whichever backend is running on 8000 -- either the
// real app, or scripts/devserver.py with a scripted provider when the free-tier
// quota is spent. The frontend cannot tell the difference, which is the point.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
