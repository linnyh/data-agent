import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 开发期：/auth /sessions 转发到本地 FastAPI（8000）
// 生产：build 产物由 FastAPI 静态托管（同源，无需 proxy）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/auth": "http://localhost:8000",
      "/sessions": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
