// Vite 配置：开发服务器 + 生产构建 + 测试（Vitest 复用同一份配置）。
//
// 为什么用 vitest/config 的 defineConfig：它在 Vite 配置类型上补了 `test` 字段，
// 这样「测试配置」和「构建配置」同处一个文件、不漂移，且类型检查能覆盖到。

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
    // 每个测试文件建一个 jsdom 要 ~218ms（26 个文件 ≈ 5.7s，是墙钟的三分之一）。
    // vmThreads + 不隔离 = 同一 worker 内复用环境；跨文件全局状态由 test-setup 的 afterEach
    // cleanup 与逐文件 vi.mock 管住（连跑稳定性与残留超时风险见 process/review-refactor.md
    // G5 节与「审计与返工」节）。纯逻辑的四个文件另走 node 环境（省 jsdom）。
    pool: "vmThreads",
    isolate: false,
    // userEvent 的重交互用例在默认 5s 下本来就紧（本机实测：即使退回 --pool=forks 的旧配置，
    // 3 次里仍有 2 次超时，超时点是 NewBatchForm / DeleteWorkdirDialog / App 外壳这类
    // 「6 次点击 + 异步取数」的用例）。放宽到 15s 只动计时器，不动任何断言对象与强度。
    testTimeout: 15_000,
  },
});
