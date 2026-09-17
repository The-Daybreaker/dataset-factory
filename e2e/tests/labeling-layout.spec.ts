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
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "打标", exact: true }).click();
  await expect(page.getByText("sample.jpg", { exact: true })).toBeVisible();

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
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("labeling-desktop.png"), fullPage: true });

  await page.getByRole("button", { name: "已完成 1", exact: true }).click();
  await expect(page.getByText("sample.jpg", { exact: true })).not.toBeVisible();
  await search.fill("sample");
  await expect(page.getByText("sample.jpg", { exact: true })).toBeVisible();
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
