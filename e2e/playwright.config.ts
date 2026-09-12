import { defineConfig } from "@playwright/test";

// E2E 配置：真实浏览器 → 真实后端服务（webServer 自动拉起 serving.py）→ 假模型端点。
// 为什么用 webServer 而不是手动起服务：CI 与本地一条命令跑齐，「服务没起」这类
// 环境问题不复现；reuseExistingServer 让本地开发时复用已起的 dsf serve 不冲突。
const PORT = 8765;

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  // 失败即停：冒烟套件不需要全量重跑浪费时间；traces 留给失败时诊断。
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    locale: "zh-CN",
  },
  webServer: {
    // uv run --project backend：用后端的 uv 环境（里面有 dataset_factory 与全部依赖）。
    // 前置 npm run build：serving.py 托管的是 frontend/dist 构建产物，不先构建就会
    // 「测的是上次构建的旧界面」（本地实锤过——改完源码忘了 build，e2e 红得莫名其妙）。
    command: "npm run build --prefix ../frontend && uv run --project ../backend python serving.py",
    url: `http://127.0.0.1:${PORT}/api/prompts`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
});
