import { mkdirSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

// 视觉与请求基线（本轮重构 L4 层的硬门）。
//
// 为什么要这一件：重构的红线是「不影响现有功能和页面」，而「页面没变」不能靠肉眼看截图。
// 这里对每一屏取两样东西存成快照：
//   1. 计算样式探针——所有可见元素的「结构路径 + 类名 + 归一化文字 + 关键计算样式 + 盒尺寸」；
//   2. 该屏实际发出的 API 请求清单（方法 + 路径）——重复请求、漏请求都会在这里显形。
// 任何一项变了，快照就红，并直接指出是哪一屏的哪个元素。截图另外落到 .verify/shots/ 供人工
// 目检（PNG 与字体渲染相关，不入仓、不做跨平台比对）。
//
// 数据面全靠路由打桩（不打真后端）：固定输入才有固定输出，时间戳与主键不会出现第二份来源。
// 探针里的数字统一折成 #，所以「3 项」这类计数变化不会把快照打成假红。
//
// 首采 / 有意更新基线：npx playwright test visual-baseline --update-snapshots

const NEWLINE = "\n";

/** 1×1 透明 PNG：素材缩略图的固定桩件。 */
const PNG_1PX =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=";

/** 路由桩表：键 = `方法 路径`（不含 query），值 = 响应体；值为 null 表示回 404。 */
const API: Record<string, unknown> = {
  "GET /api/prompts": [
    { name: "详细描述", description: "通用详细描述提示词" },
    { name: "简短描述", description: "一句话描述" },
  ],
  "GET /api/prompts/详细描述": {
    name: "详细描述",
    description: "通用详细描述提示词",
    body: "请用中文详细描述这张图的主体、姿态、背景与光线。",
  },
  "GET /api/skills": [
    { name: "caption-style", description: "风格约束", enabled: true, body_chars: 1234 },
    { name: "anatomy-check", description: "结构检查", enabled: false, body_chars: 567 },
  ],
  "GET /api/config": {
    name: "default",
    base_url: "https://api.example.test/v1",
    model: "example-caption-model",
    api_key_configured: true,
    key_source: "credentials",
  },
  "GET /api/endpoints": [
    {
      name: "default",
      base_url: "https://api.example.test/v1",
      model: "example-caption-model",
      api_format: "openai-chat",
      has_api_key: true,
      is_active: true,
      request_params: {},
    },
    {
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
      prompt: "详细描述",
      skills: ["caption-style"],
      endpoint: "default",
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

/** 截图落点：仓外 .verify/shots/current（PNG 与字体渲染绑定，不入仓、只做本机对照）。 */
function shotPath(name: string): string {
  const dir = path.resolve(process.cwd(), "../.verify/shots/current");
  mkdirSync(dir, { recursive: true });
  return path.join(dir, `${name}.png`);
}

/** 装上路由桩，并把每一屏实际请求到的 API 路径记进 sink（供快照比对）。 */
async function stubApi(page: Page, sink: string[]): Promise<void> {
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
    const body: unknown = API[key];
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

/** 探针前的稳定化：关掉过渡与进入动画并等两帧。
 *
 * 实测原因：页面大量元素带 `transition-colors` 与入场动画，取数时刻落在过渡中间就会
 * 拿到插值色（同一份代码两次采集色差可达 60/255），快照因此假红。稳定化只影响取值时机，
 * 不改任何布局与类名；探针本身不采集 transition/animation 属性，注入的样式不进快照。
 */
async function settle(page: Page): Promise<void> {
  await page.addStyleTag({
    content: "*,*::before,*::after{transition:none !important;animation:none !important}",
  });
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            setTimeout(resolve, 60);
          });
        });
      }),
  );
}

/**
 * 取一屏的计算样式探针。
 *
 * @param page 已经渲染好目标状态的页面对象。
 * @returns 每行一个可见元素：结构路径 + 属性 + 类名 + 归一化文字 + 关键计算样式 + 盒尺寸。
 */
async function probe(page: Page): Promise<string> {
  const rows = await page.evaluate(() => {
    const css = getComputedStyle;
    const pathOf = (element: Element): string => {
      const parts: string[] = [];
      let node: Element | null = element;
      while (node && node !== document.body) {
        const parent: Element | null = node.parentElement;
        const index = parent
          ? Array.prototype.indexOf.call(
              parent.children,
              node satisfies Element as Element,
            )
          : 0;
        parts.unshift(`${node.tagName.toLowerCase()}#${index}`);
        node = parent;
      }
      return parts.join("/");
    };
    const visible = (element: Element): boolean => {
      const computed = css(element);
      const rect = element.getBoundingClientRect();
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        computed.visibility !== "hidden" &&
        computed.display !== "none" &&
        Number(computed.opacity) > 0.01
      );
    };
    const digits = (value: string): string => value.replace(/\d/g, "#");
    const ownText = (element: Element): string => {
      const texts = Array.from(element.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent ?? "");
      return digits(texts.join(" ").replace(/\s+/g, " ").trim());
    };
    return Array.from(document.body.querySelectorAll("*"))
      .filter(visible)
      .map((element) => {
        const computed = css(element);
        const rect = element.getBoundingClientRect();
        return [
          pathOf(element),
          element.getAttribute("data-slot") ?? "",
          element.getAttribute("aria-label") ?? "",
          element.getAttribute("role") ?? "",
          (element.getAttribute("class") ?? "").replace(/\s+/g, " ").trim(),
          ownText(element),
          `${Math.round(rect.x)},${Math.round(rect.y)} ${Math.round(rect.width)}x${Math.round(rect.height)}`,
          computed.fontSize,
          computed.fontWeight,
          computed.color,
          computed.backgroundColor,
          `${computed.borderTopWidth}/${computed.borderRightWidth}/${computed.borderBottomWidth}/${computed.borderLeftWidth}`,
          computed.borderTopColor,
          computed.borderTopLeftRadius,
          `${computed.paddingTop} ${computed.paddingRight} ${computed.paddingBottom} ${computed.paddingLeft}`,
          computed.gap,
          computed.display,
        ].join(" | ");
      });
  });
  return rows.join("\n");
}

test.describe("视觉与请求基线", () => {
  /** 起一屏：设视口、装路由桩、打开首页并等字体就绪。 */
  async function boot(page: Page): Promise<string[]> {
    const sink: string[] = [];
    await page.setViewportSize({ width: 1440, height: 900 });
    await stubApi(page, sink);
    await page.goto("/");
    await page.waitForFunction(() => document.fonts.status === "loaded");
    return sink;
  }

  /** 采集当前屏：探针快照 + 请求清单快照 + 落一张截图供目检。 */
  async function snap(page: Page, sink: string[], name: string): Promise<void> {
    await settle(page);
    const styles = await probe(page);
    expect(styles + NEWLINE).toMatchSnapshot(`${name}.styles.txt`);
    expect(sink.join(NEWLINE) + NEWLINE).toMatchSnapshot(`${name}.requests.txt`);
    await page.screenshot({ path: shotPath(name), animations: "disabled" });
  }

  /** 进打标页：桩数据里只有一个工作目录与一个批次，页面会自动选中。 */
  async function openLabeling(page: Page): Promise<void> {
    await page.getByRole("button", { name: "打标", exact: true }).click();
    await expect(page.getByRole("region", { name: "批次概览" })).toBeVisible();
  }

  test("01 工作台首屏", async ({ page }) => {
    const sink = await boot(page);
    await expect(page.getByRole("textbox", { name: "策略名称" })).toBeVisible();
    await snap(page, sink, "01-workbench");
  });

  test("02 工作台-提示词下拉", async ({ page }) => {
    const sink = await boot(page);
    await page.getByRole("button", { name: "切换提示词" }).click();
    await snap(page, sink, "02-workbench-prompt-menu");
  });

  test("03 工作台-端点切换器", async ({ page }) => {
    const sink = await boot(page);
    await page.getByRole("button", { name: "端点配置切换器" }).click();
    await snap(page, sink, "03-workbench-endpoint-menu");
  });

  test("04 设置-端点段", async ({ page }) => {
    const sink = await boot(page);
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await snap(page, sink, "04-settings-endpoint");
  });

  test("05 设置-服务段", async ({ page }) => {
    const sink = await boot(page);
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("button", { name: "服务运行", exact: true }).click();
    await snap(page, sink, "05-settings-service");
  });

  test("06 设置-Skill 段", async ({ page }) => {
    const sink = await boot(page);
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("button", { name: "技能", exact: true }).click();
    await snap(page, sink, "06-settings-skill");
  });

  test("07 打标概览", async ({ page }) => {
    const sink = await boot(page);
    await openLabeling(page);
    await snap(page, sink, "07-labeling-overview");
  });

  test("08 打标-素材预览", async ({ page }) => {
    const sink = await boot(page);
    await openLabeling(page);
    await page.getByRole("button", { name: "alpha.png", exact: true }).click();
    await snap(page, sink, "08-labeling-preview");
  });

  test("09 打标-暗色概览", async ({ page }) => {
    const sink = await boot(page);
    await openLabeling(page);
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    await snap(page, sink, "09-labeling-dark");
  });

  test("10 打标-新建跑批弹窗", async ({ page }) => {
    const sink = await boot(page);
    await openLabeling(page);
    await page.getByRole("button", { name: "新建跑批", exact: true }).click();
    await snap(page, sink, "10-labeling-new-batch");
  });

  test("11 打标-工作目录设置抽屉", async ({ page }) => {
    const sink = await boot(page);
    await openLabeling(page);
    await page.getByRole("button", { name: "选择工作目录与批次" }).click();
    await page.getByRole("button", { name: /工作目录设置/ }).first().click();
    await snap(page, sink, "11-labeling-workdir-settings");
  });

  test("12 打标-策略快照弹窗", async ({ page }) => {
    const sink = await boot(page);
    await openLabeling(page);
    await page.getByRole("button", { name: "快照", exact: true }).click();
    await snap(page, sink, "12-labeling-snapshot");
  });
});
