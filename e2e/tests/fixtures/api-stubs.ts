import type { Page } from "@playwright/test";

// 浏览器端测试的共用桩表：视觉基线（visual-baseline.spec.ts）与性能探针（perf-probe.spec.ts）
// 跑的是同一份假数据，两边才谈得上可比。数据面全靠路由打桩（不打真后端）：固定输入才有固定
// 输出，时间戳与主键不会出现第二份来源。
//
// 改这张表要同时想清楚两屏快照：视觉基线的请求清单快照会记下每一屏实际请求了什么。

/** 1×1 透明 PNG：素材缩略图的固定桩件。 */
const PNG_1PX =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=";

/** 路由桩表：键 = `方法 路径`（不含 query），值 = 响应体；值为 null 表示回 404。 */
export const API_STUBS: Record<string, unknown> = {
  "GET /api/prompts": [
    { id: "p-1", name: "详细描述", description: "通用详细描述提示词" },
    { id: "p-2", name: "简短描述", description: "一句话描述" },
  ],
  "GET /api/prompts/p-1": {
    id: "p-1",
    name: "详细描述",
    description: "通用详细描述提示词",
    body: "请用中文详细描述这张图的主体、姿态、背景与光线。",
  },
  "GET /api/skills": [
    { id: "k-1", name: "caption-style", description: "风格约束", enabled: true, body_chars: 1234 },
    { id: "k-2", name: "anatomy-check", description: "结构检查", enabled: false, body_chars: 567 },
  ],
  "GET /api/config": {
    id: "e-1",
    name: "default",
    base_url: "https://api.example.test/v1",
    model: "example-caption-model",
    api_key_configured: true,
    key_source: "credentials",
  },
  "GET /api/endpoints": [
    {
      id: "e-1",
      name: "default",
      base_url: "https://api.example.test/v1",
      model: "example-caption-model",
      api_format: "openai-chat",
      has_api_key: true,
      is_active: true,
      request_params: {},
    },
    {
      id: "e-2",
      name: "offline",
      base_url: "http://127.0.0.1:9/v1",
      model: "offline-model",
      api_format: "openai-chat",
      has_api_key: false,
      is_active: false,
      request_params: { temperature: 0.7 },
    },
  ],
  "GET /api/service": {
    version: "0.1.0",
    host: "127.0.0.1",
    port: 8765,
    started_at: "2026-01-01T00:00:00+00:00",
    log_file: "/tmp/dsf/logs/server.log",
  },
  "GET /api/service/logs": { lines: ["INFO 启动完成"], truncated: false },
  "GET /api/sessions/latest": null,
  "GET /api/filesystem/capabilities": {
    supported: true,
    home: "/home",
    separator: "/",
    roots: ["/"],
  },
  "GET /api/strategies": [
    {
      id: "st1",
      name: "基线策略",
      description: "视觉基线用策略",
      enabled: true,
      prompt_id: "p-1",
      skill_ids: ["k-1"],
      endpoint_id: "e-1",
      updated_at: "2026-01-01T00:00:00+00:00",
    },
  ],
  "GET /api/workdirs": [
    {
      id: "probe",
      title: "视觉基线工作目录",
      path: "/data/visual-probe",
      last_used_at: "2026-01-01T00:00:00+00:00",
    },
  ],
  "GET /api/workdirs/probe": {
    id: "probe",
    title: "视觉基线工作目录",
    path: "/data/visual-probe",
    last_used_at: "2026-01-01T00:00:00+00:00",
  },
  "GET /api/workdirs/probe/stats": { asset_count: 3, asset_bytes: 204 },
  "GET /api/workdirs/probe/batches": [
    {
      id: "s1",
      seq: 1,
      name: "基线批次",
      active: true,
      created_at: "2026-01-01T00:00:00+00:00",
      description: "用于快照的批次",
      product_count: 2,
      run_status: "completed",
      run_done: 2,
      run_total: 3,
    },
  ],
  "GET /api/workdirs/probe/batches/s1/items": {
    batch: 1,
    query: "",
    groups: {
      done: [
        {
          item: "alpha",
          name: "alpha.png",
          status: "done",
          media: "image",
          can_retry: true,
          in_retry: false,
        },
      ],
      pending: [
        {
          item: "beta",
          name: "beta.png",
          status: "pending",
          media: "image",
          can_retry: false,
          in_retry: false,
        },
      ],
      failed: [
        {
          item: "gamma",
          name: "gamma.mp4",
          status: "failed",
          media: "video",
          can_retry: true,
          in_retry: true,
        },
      ],
      excluded: [],
      missing_asset: [],
      missing_product: [],
    },
  },
  "GET /api/workdirs/probe/batches/s1/runs/current": null,
  "GET /api/workdirs/probe/batches/s1/runs/latest": {
    record: null,
    log_path: null,
    items_path: null,
  },
  "GET /api/workdirs/probe/batches/s1/snapshot": {
    built_at: "2026-01-01T00:00:00+00:00",
    changed: false,
    endpoint: {
      id: "e-1",
      name: "default",
      model: "example-caption-model",
      api_format: "openai-chat",
      base_url: "https://api.example.test/v1",
      request_params: {},
      sha256: "endpoint",
    },
    prompt: { name: "详细描述", body: "请用中文详细描述这张图。", sha256: "prompt" },
    skills: [{ name: "caption-style", body: "风格约束正文。", sha256: "skill" }],
    recorded_sha256: "snapshot",
    sha256: "snapshot",
    tool_version: "0.1.0",
  },
  "GET /api/workdirs/probe/export/plan": {
    batch: 1,
    included: [
      {
        item: "alpha",
        name: "alpha.png",
        asset_name: "001.png",
        caption_name: "001.txt",
        asset_bytes: 68,
        caption_bytes: 17,
        integrity: "valid",
      },
    ],
    excluded: [],
    total_bytes: 85,
    sequential: true,
    non_ascii_names: false,
  },
  "GET /api/workdirs/probe/cleanup-preview": {
    candidates: [],
    total_bytes: 0,
    isolated_to: "/data/visual-probe/.dsf/quarantine",
  },
  "GET /api/workdirs/probe/delete-preview": {
    path: "/data/visual-probe",
    asset_count: 3,
    product_count: 2,
    bytes: 204,
  },
};

/**
 * 装上路由桩：键 = `方法 路径`（不含 query），值为 null 表示回 404；
 * overrides 用来按用例替换个别端点（如喂一份 3000 条的清单）。
 * 每一屏实际请求到的路径记进 sink（供快照比对）。
 */
export async function stubApi(
  page: Page,
  sink: string[] = [],
  overrides: Record<string, unknown> = {},
): Promise<void> {
  await page.route("**/api/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = decodeURIComponent(url.pathname);
    const key = `${request.method()} ${pathname}`;
    sink.push(`${request.method()} ${pathname}`);
    if (pathname.endsWith("/asset")) {
      route.fulfill({ contentType: "image/png", body: Buffer.from(PNG_1PX, "base64") });
      return;
    }
    if (pathname.endsWith("/txt")) {
      route.fulfill({ contentType: "text/plain", body: "一段基线产物描述。" });
      return;
    }
    const body: unknown = key in overrides ? overrides[key] : API_STUBS[key];
    if (body === undefined || body === null) {
      route.fulfill({
        status: 404,
        contentType: "application/problem+json",
        body: JSON.stringify({
          type: "https://example.test/probe-unstubbed",
          title: "桩表未覆盖",
          status: 404,
          detail: key,
        }),
      });
      return;
    }
    route.fulfill({ json: body });
  });
}
