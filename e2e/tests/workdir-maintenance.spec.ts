import { expect, test } from "@playwright/test";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { isolatedBaseURL } from "./fixtures/isolated-servers";

// 本文件打真后端，用自己那份服务与数据根：搬迁/删除的维护锁也落在自己的数据根里，
// 不会被别的文件的操作撞上。
test.use({ baseURL: isolatedBaseURL("workdir-maintenance.spec.ts") });

test("工作目录搬迁经界面确认后保留素材与快照并更新登记", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const source = testInfo.outputPath("original");
  const target = testInfo.outputPath("renamed-parent", "original");
  await mkdir(source, { recursive: true });
  const bytes = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64");
  await writeFile(path.join(source, "sample.png"), bytes);
  const create = await request.post("/api/workdirs", { data: { path: source, title: "搬迁验证" } });
  expect(create.status()).toBe(202);
  const accepted = await create.json();
  const wid = accepted.workdir.id;
  await expect.poll(async () => (await (await request.get(`/api/tasks/${accepted.task_id}`)).json()).status).toBe("succeeded");
  const prompts = await (await request.get("/api/prompts")).json();
  const batch = await request.post(`/api/workdirs/${wid}/batches`, { data: { type: "scratch", name: "搬迁验证", endpoint: "default", prompt: prompts[0].name, skills: [] } });
  expect(batch.ok()).toBeTruthy();
  const snapshot = await readFile(path.join(source, ".dsf", "strategies", "s1.json"));
  const moves: unknown[] = [];
  const errors: string[] = [];
  page.on("request", (req) => { if (req.method() === "POST" && req.url().endsWith("/relocate")) moves.push(req.postDataJSON()); });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "打标", exact: true }).click();
  await page.getByRole("button", { name: "选择工作目录与批次" }).click();
  await page.getByRole("button", { name: "工作目录设置 搬迁验证", exact: true }).click();
  await expect(page.getByRole("region", { name: "工作目录设置", exact: true })).toBeVisible();
  // 「素材 N · 产物 M」这一段来自 stats 异步请求：弹窗一打开只有路径，CI 冷启动首访
  // 实测 5 秒默认预算内还没到（run 35433472710 报错正文：Locator 解析到了 section、
  // 14 次重试都只读到「基本信息路径…」）。与同文件搬迁状态断言同口径放宽到 30 秒，断言内容一字未改。
  await expect(page.getByRole("region", { name: "基本信息" })).toContainText("素材 1 · 产物 0", {
    timeout: 30_000,
  });
  expect(await (await request.get(`/api/workdirs/${wid}/stats`)).json()).toEqual({ asset_count: 1, asset_bytes: bytes.length });
  await expect(page.getByText("产物 0 / 1", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "改名策略 搬迁验证", exact: true }).click();
  const rename = page.getByRole("textbox", { name: "策略名称 s1" });
  await rename.fill("搬迁改名");
  await rename.press("Enter");
  await expect(page.getByRole("button", { name: "改名策略 搬迁改名", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "新增策略", exact: true }).click();
  const strategyDialog = page.getByRole("dialog", { name: "新增策略", exact: true });
  await strategyDialog.getByRole("textbox", { name: "策略名", exact: true }).fill("设置新增");
  await strategyDialog.getByRole("button", { name: "创建策略", exact: true }).click();
  await expect(strategyDialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "改名策略 设置新增", exact: true })).toBeVisible();
  expect((await request.get(`/api/workdirs/${wid}/batches/s2/runs/current`)).status()).toBe(200); // L3：空闲 = 200 + null
    expect(await (await request.get(`/api/workdirs/${wid}/batches/s2/runs/current`)).json()).toBeNull();
  await page.getByRole("button", { name: "返回打标页", exact: true }).click();
  await page.getByRole("button", { name: "选择工作目录与批次" }).click();
  await page.getByRole("button", { name: "新增策略 搬迁验证", exact: true }).click();
  await expect(strategyDialog).toBeVisible();
  await strategyDialog.getByRole("textbox", { name: "策略名", exact: true }).fill("下拉新增");
  await expect(strategyDialog.getByRole("button", { name: "创建策略", exact: true })).toHaveCSS("color", "rgb(255, 255, 255)");
  const overlay = page.locator('[data-slot="dialog-overlay"]');
  await expect(overlay).toHaveCSS("background-color", /^(rgba\(0, 0, 0, 0\.32\)|oklab\(0 0 0 \/ 0\.32\))$/);
  await page.screenshot({ path: testInfo.outputPath("new-strategy.png"), animations: "disabled" });
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await expect(overlay).toHaveCSS("background-color", /^(rgba\(0, 0, 0, 0\.55\)|oklab\(0 0 0 \/ 0\.55\))$/);
  await expect(strategyDialog.getByRole("button", { name: "创建策略", exact: true })).toHaveCSS("color", "rgb(255, 255, 255)");
  await page.screenshot({ path: testInfo.outputPath("new-strategy-dark.png"), animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 });
  const bounds = await strategyDialog.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(15);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(375);
  await page.screenshot({ path: testInfo.outputPath("new-strategy-mobile.png"), animations: "disabled" });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.evaluate(() => document.documentElement.classList.remove("dark"));
  await strategyDialog.getByRole("button", { name: "创建策略", exact: true }).click();
  await expect(strategyDialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "选择工作目录与批次" })).toContainText("下拉新增");
  const batchList = await (await request.get(`/api/workdirs/${wid}/batches`)).json();
  expect(batchList.map((entry: { id: string; name: string }) => [entry.id, entry.name])).toEqual([["s1", "搬迁改名"], ["s2", "设置新增"], ["s3", "下拉新增"]]);
  expect((await request.get(`/api/workdirs/${wid}/batches/s3/runs/current`)).status()).toBe(200); // L3：空闲 = 200 + null
  expect(JSON.parse(await readFile(path.join(source, ".dsf", "strategies", "s3.json"), "utf8"))).toHaveProperty("prompt");
  await page.getByRole("button", { name: "选择工作目录与批次" }).click();
  await page.getByRole("button", { name: "工作目录设置 搬迁验证", exact: true }).click();
  await expect(page.getByRole("button", { name: "修改路径", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "修改路径", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "修改路径", exact: true });
  await dialog.getByRole("button", { name: "选择目标父目录" }).click();
  const picker = page.getByRole("dialog", { name: "选择目录", exact: true });
  await expect(picker.getByText(/当前后端：/)).toBeVisible();
  await picker.getByRole("textbox", { name: "服务器路径" }).fill(testInfo.outputPath());
  await picker.getByRole("button", { name: "跳转", exact: true }).click();
  await expect(picker.getByRole("button", { name: "original", exact: true })).toBeVisible();
  await picker.getByRole("button", { name: "新建目录", exact: true }).click();
  await picker.getByRole("textbox", { name: "目录名称", exact: true }).fill("new-parent");
  await picker.getByRole("button", { name: "创建", exact: true }).click();
  await expect(picker.getByRole("textbox", { name: "服务器路径" })).toHaveValue(testInfo.outputPath("new-parent"));
  expect((await stat(testInfo.outputPath("new-parent"))).isDirectory()).toBeTruthy();
  await picker.getByRole("button", { name: "上一级" }).click();
  await picker.getByRole("button", { name: "重命名 new-parent", exact: true }).click();
  await picker.getByRole("textbox", { name: "重命名", exact: true }).fill("renamed-parent");
  await picker.getByRole("button", { name: "保存", exact: true }).click();
  await expect(picker.getByRole("textbox", { name: "服务器路径" })).toHaveValue(testInfo.outputPath("renamed-parent"));
  await expect(stat(testInfo.outputPath("new-parent"))).rejects.toThrow();
  await expect(picker).toHaveCSS("width", "620px");
  await page.screenshot({ path: testInfo.outputPath("directory-picker.png"), animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 });
  const pickerBounds = await picker.boundingBox();
  expect(pickerBounds!.x).toBeGreaterThanOrEqual(15);
  expect(pickerBounds!.x + pickerBounds!.width).toBeLessThanOrEqual(375);
  await page.screenshot({ path: testInfo.outputPath("directory-picker-mobile.png"), animations: "disabled" });
  await page.setViewportSize({ width: 1280, height: 720 });
  await picker.getByRole("button", { name: "选择此目录" }).click();
  await expect(picker).toHaveCount(0);
  await expect(dialog.getByRole("textbox", { name: "目标目录" })).toHaveValue(target);
  await dialog.getByRole("button", { name: "修改路径", exact: true }).click();
  expect(moves).toEqual([]);
  expect((await stat(source)).isDirectory()).toBeTruthy();
  await dialog.getByRole("button", { name: "确认搬迁", exact: true }).click();
  // 搬迁是「复制 + 逐文件回读校验」的后台任务，界面那条状态行要等它跑完才出现。
  // 默认 5s 在整条门禁连跑（前面刚跑完 pytest / Vitest / 构建）时不够用，实测同一份代码
  // 单跑必过、跟在门禁后面偶发不出现（2026-09-19 三次 in-suite 失败均是等待预算不足，
  // 报错都是 element(s) not found 而非搬迁失败）。放宽等待，断言内容与后面的磁盘核对一字未改。
  // 2026-09-21 再放宽到 60s：in-suite 跑时搬迁任务与并行 worker 抢 CPU，「正在搬迁 · 0%」
  // 卡 30s 的形态与 2026-09-19 相同（等待预算不足，非搬迁失败）。
  await expect(dialog.getByRole("status")).toContainText("已搬迁到", {
    timeout: 60_000,
  });
  expect(moves).toEqual([{ path: target }]);
  expect(await readFile(path.join(target, "sample.png"))).toEqual(bytes);
  expect(await readFile(path.join(target, ".dsf", "strategies", "s1.json"))).toEqual(snapshot);
  await expect(stat(source)).rejects.toThrow();
  const info = await (await request.get(`/api/workdirs/${wid}`)).json();
  expect(info.id).toBe(wid);
  expect(info.path).toBe(target);
  await dialog.getByRole("button", { name: "关闭", exact: true }).first().click();
  await expect(page.getByRole("region", { name: "基本信息" })).toContainText(target);
  await writeFile(path.join(target, "s1__lost.txt"), "orphan caption");
  await writeFile(path.join(target, "s1__other.txt"), "retained caption");
  await page.getByRole("button", { name: "查看无素材产物清单" }).click();
  const cleanup = page.getByRole("dialog", { name: "清理无素材产物", exact: true });
  await cleanup.getByRole("checkbox", { name: "s1__lost.txt", exact: true }).check();
  await cleanup.getByRole("button", { name: "清理（1）", exact: true }).click();
  expect(await readFile(path.join(target, "s1__lost.txt"), "utf8")).toBe("orphan caption");
  await cleanup.getByRole("button", { name: "确认清理", exact: true }).click();
  await expect(cleanup.getByRole("status")).toContainText("已移出 1 项");
  await expect(stat(path.join(target, "s1__lost.txt"))).rejects.toThrow();
  expect(await readFile(path.join(target, "s1__other.txt"), "utf8")).toBe("retained caption");
  expect(await readFile(path.join(target, "sample.png"))).toEqual(bytes);
  await cleanup.getByRole("button", { name: "关闭", exact: true }).first().click();
  const deletes: unknown[] = [];
  page.on("request", (req) => { if (req.method() === "DELETE" && req.url().endsWith(`/workdirs/${wid}`)) deletes.push(req.postDataJSON()); });
  await page.getByRole("button", { name: "删除工作目录", exact: true }).click();
  const deletion = page.getByRole("dialog", { name: "删除工作目录？", exact: true });
  await expect(deletion.getByText(target, { exact: true })).toBeVisible();
  expect(deletes).toEqual([]);
  expect((await stat(target)).isDirectory()).toBeTruthy();
  await deletion.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(deletion).toHaveCount(0);
  expect(deletes).toEqual([{ confirmed_path: target }]);
  await expect(stat(target)).rejects.toThrow();
  expect((await request.get(`/api/workdirs/${wid}`)).status()).toBe(404);
  await expect(page.getByRole("region", { name: "工作目录设置", exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("workdir-settings.png"), fullPage: true, animations: "disabled" });
});
