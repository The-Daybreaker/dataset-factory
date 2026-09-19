import { expect, test } from "@playwright/test";

test("打标页长名称下顶栏与条目列保持原型尺寸和对齐", async ({ page }, testInfo) => {
  await page.route("**/api/workdirs", (route) => route.fulfill({ json: [{
    id: "layout", title: "用于布局验证的长工作目录名称", path: "/layout", last_used_at: 0,
  }] }));
  await page.route("**/api/workdirs/layout/batches", (route) => route.fulfill({ json: [{
    id: "s1", seq: 1, name: "用于布局验证的长策略名称", active: true,
    created_at: "", description: "", product_count: 1,
  }] }));
  await page.route("**/api/workdirs/layout/batches/s1/items", (route) => route.fulfill({ json: {
    batch: 1, query: "", groups: { done: [{ item: "sample", name: "sample.jpg",
      status: "done", media: "image", can_retry: true, in_retry: false }] },
  } }));
  await page.route("**/runs/current", (route) => route.fulfill({ status: 404, json: { detail: "没有运行" } }));
  await page.route("**/runs/latest", (route) => route.fulfill({ json: {
    record: null, log_path: null, items_path: null,
  } }));
  await page.route("**/batches/s1/snapshot", (route) => route.fulfill({ json: {
    built_at: "2026-09-18T00:00:00Z", changed: false,
    endpoint: { name: "Example", model: "caption-model", api_format: "openai-chat",
      base_url: "https://example.test/v1", request_params: {}, sha256: "endpoint" },
    prompt: { name: "Detailed caption", body: "Describe the image.", sha256: "prompt" },
    skills: [{ name: "caption-style", body: "Use plain language.", sha256: "skill" }],
    recorded_sha256: "snapshot", sha256: "snapshot", tool_version: "0.1.0",
  } }));
  await page.route("**/export/plan?*", (route) => route.fulfill({ json: {
    batch: 1, included: [{ item: "sample", name: "sample.jpg", asset_name: "001.jpg",
      caption_name: "001.txt", asset_bytes: 68, caption_bytes: 17, integrity: "valid" }],
    excluded: [], total_bytes: 85, sequential: true, non_ascii_names: false,
  } }));
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "打标", exact: true }).click();
  await expect(page.getByRole("button", { name: "sample.jpg", exact: true })).toBeVisible();

  const column = page.getByRole("complementary", { name: "素材条目" });
  const selector = page.getByRole("button", { name: "选择工作目录与批次" });
  const create = page.getByRole("button", { name: "新建跑批", exact: true });
  const search = page.getByRole("textbox", { name: "搜索素材" });
  const bounds = await Promise.all([column.boundingBox(), selector.boundingBox(), create.boundingBox(), search.locator("..").boundingBox()]);
  const [listBox, selectorBox, createBox, searchBox] = bounds;
  if (!listBox || !selectorBox || !createBox || !searchBox) throw new Error("缺少布局元素");
  expect(listBox.width).toBe(320);
  expect(selectorBox.x).toBe(listBox.x);
  expect(selectorBox.height).toBe(34);
  expect(createBox.height).toBe(34);
  expect(createBox.width).toBe(34);
  expect(createBox.x + createBox.width).toBe(listBox.x + listBox.width - 12);
  expect(searchBox.height).toBe(26);
  expect(selectorBox.x + selectorBox.width).toBeLessThan(createBox.x);
  const context = page.getByRole("region", { name: "策略配置" });
  // 端点章 title = 「端点 · 模型 · 健康信息」（C1 探测点），健康段随探测结果变化，按前缀匹配。
  const endpoint = context.getByTitle(/Example · caption-model/);
  await expect(endpoint).toBeVisible();
  await expect(endpoint).toHaveCSS("height", "26px");
  await expect(endpoint).toHaveCSS("border-radius", "12px");
  await expect(endpoint).toHaveCSS("border-top-width", "0px");
  expect((await context.boundingBox())?.x).toBe(listBox.x + listBox.width + 16);
  expect((await page.getByRole("region", { name: "条目汇总" }).locator("dl").boundingBox())?.width).toBe(460);
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("labeling-desktop.png"), fullPage: true });

  await page.getByRole("button", { name: "已完成 1", exact: true }).click();
  await expect(page.getByRole("button", { name: "sample.jpg", exact: true })).not.toBeVisible();
  await search.fill("sample");
  await expect(page.getByRole("button", { name: "sample.jpg", exact: true })).toBeVisible();
  await page.route("**/items/sample/txt", (route) => route.fulfill({ contentType: "text/plain", body: "A sample caption." }));
  await page.route("**/items/sample/asset", (route) => route.fulfill({
    contentType: "image/png",
    body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64"),
  }));
  await page.getByRole("button", { name: "sample.jpg", exact: true }).click();
  const image = page.getByRole("img", { name: "sample.jpg" });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(1);
  await expect(page.getByText("A sample caption.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "折叠为小图" }).click();
  expect((await image.locator("..").boundingBox())?.height).toBe(76);
  await page.screenshot({ path: testInfo.outputPath("labeling-preview-folded.png"), fullPage: true });
  await page.getByRole("button", { name: "展开素材" }).click();
  expect((await image.locator("..").boundingBox())?.height).toBe(384);
  await page.getByRole("button", { name: "返回概览" }).click();
  await expect(image).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileColumn = await column.boundingBox();
  const mobileCreate = await create.boundingBox();
  if (!mobileColumn || !mobileCreate) throw new Error("缺少窄屏布局元素");
  expect(mobileColumn.width).toBe(358);
  expect(mobileCreate.x + mobileCreate.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.screenshot({ path: testInfo.outputPath("labeling-mobile.png"), fullPage: true });
  expect(errors).toEqual([]);
});
