import { defineConfig } from "@playwright/test";
import { ISOLATED_SPEC_FILES, isolatedPort } from "./tests/fixtures/isolated-servers";

// E2E 配置：真实浏览器 → 真实后端服务（webServer 自动拉起 serving.py）→ 假模型端点。
//
// 服务分两种，共同点是「一个测试文件一份数据根」：
//   - 共用服务（8765）：只托管前端静态文件，给打桩套件用——它们的 /api 全被
//     page.route 拦掉，不发真后端请求、也不写数据根。
//   - 专属服务（8801 起，一个 spec 文件一个）：打真后端的文件各有各的服务与数据根，
//     谁都不碰谁的东西。这就是「单独跑必绿、连跑偶发红」的解药。
//
// 前端构建挂在共用服务那条命令上，不放进 globalSetup：已装机型的源码
// （playwright/lib/runner/index.js 的 createGlobalSetupTasks）里，插件启动——含起服务
// ——排在 globalSetup **之前**，构建放进 globalSetup 会赶不上服务启动。
const PORT = 8765;

/** 起一份被测服务。端口不同，serving.py 就会自己建一份不同的临时数据根。 */
const serve = (port: number) =>
  `uv run --project ../backend python serving.py --port ${port}`;

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  // 失败即停：冒烟套件不需要全量重跑浪费时间；traces 留给失败时诊断。
  fullyParallel: false,
  workers: 1,
  // 快照名不带平台后缀：探针取的是 CSS 计算值，Win/Linux 上同一份代码应当同一结果。
  snapshotPathTemplate: "{testDir}/{testFileDir}/{testFileName}-snapshots/{arg}{ext}",
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    locale: "zh-CN",
  },
  webServer: [
    {
      // 共用服务：打桩套件只借它托管前端静态文件。
      //
      // 本地保留这里的 npm run build：serving.py 托管的是 frontend/dist 构建产物，
      // 改完源码忘了构建就会「测的是上次构建的旧界面」（本机实锤过）。CI 上 dist 已由
      // 「Build frontend」步骤产出入 artifact，再构建一遍纯属重复。
      command: process.env.CI
        ? serve(PORT)
        : `npm run build --prefix ../frontend && ${serve(PORT)}`,
      url: `http://127.0.0.1:${PORT}/api/prompts`,
      name: "shared",
      // 不复用已存在的服务：8765 上若残留着上次没退干净的服务，它的数据根是旧的、
      // 也不会被新进程的清扫碰到，「每次都从干净状态开始」这条前提会悄悄失效。
      // 直接报错，比静默跑在旧数据上强。
      reuseExistingServer: false,
      timeout: 120_000,
    },
    ...ISOLATED_SPEC_FILES.map((file) => ({
      command: serve(isolatedPort(file)),
      // 探针打 `/` 而不是 `/api/...`：前端产物缺失时应用不挂静态目录（app.py 里
      // `if directory.is_dir()`），此时 `/api/...` 照样 200、界面却打不开；而 `/` 会回
      // 404，Playwright 不认 404，于是它会一直等到产物就绪——构建与起服务并发起跑时
      // 的先后问题，被这条探针顺手解决了。
      url: `http://127.0.0.1:${isolatedPort(file)}/`,
      name: file,
      reuseExistingServer: false,
      timeout: 120_000,
    })),
  ],
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
});
