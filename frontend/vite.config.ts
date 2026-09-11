// Vite 配置：开发服务器 + 生产构建 + 测试（Vitest 复用同一份配置）。
//
// 为什么用 vitest/config 的 defineConfig：它在 Vite 配置类型上补了 `test` 字段，
// 这样「测试配置」和「构建配置」同处一个文件、不漂移，且类型检查能覆盖到。
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // 开发期由 Vite 提供前端、后端仍跑 dsf serve：把 /api 转发到后端，
    // 这样前端代码里的相对路径 /api/* 在开发和「构建后由后端托管」两种情况下都一样。
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
