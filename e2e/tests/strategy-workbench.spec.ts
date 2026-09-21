import { expect, test } from "@playwright/test";
import { isolatedBaseURL } from "./fixtures/isolated-servers";

// 本文件打真后端，用自己那份服务与数据根：别处建的提示词不会混进策略库。
test.use({ baseURL: isolatedBaseURL("strategy-workbench.spec.ts") });

test("策略保存、切换和对话使用同一组合，工作台匹配两栏原型", async ({ page, request }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "切换提示词" }).click();
  await page.getByRole("button", { name: "新建提示词" }).click();
  await page.getByLabel("名称", { exact: true }).fill("strategy-workbench-prompt");
  await page.getByLabel("描述", { exact: true }).fill("策略调试");
  await page.getByLabel("正文（Markdown）").fill("客观描述可见画面。");
  // L9（2026-09-21 审计）：锁定改为「列表照开、点了才提示」——触发钮不再禁用。
  await expect(page.getByRole("button", { name: "切换策略" })).toBeEnabled();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("已保存提示词「strategy-workbench-prompt」")).toBeVisible();
  await page.getByLabel("策略名称", { exact: true }).fill("E2E 详细策略");
  await page.getByLabel("策略描述", { exact: true }).fill("真实往返");
  await page.getByRole("button", { name: "保存策略" }).click();
  await expect(page.getByRole("button", { name: "切换策略" })).toBeEnabled();
  const response = await request.get("/api/strategies");
  expect(response.ok()).toBeTruthy();
  const entries = await response.json();
  expect(entries).toEqual(expect.arrayContaining([expect.objectContaining({
    name: "E2E 详细策略", description: "真实往返", prompt: "strategy-workbench-prompt", available: true,
  })]));
  await page.getByRole("button", { name: "切换策略" }).click();
  await page.getByRole("button", { name: "新建策略" }).click();
  await expect(page.getByLabel("策略名称", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "切换策略" }).click();
  await page.getByRole("button", { name: /E2E 详细策略.*真实往返/ }).click();
  await expect(page.getByLabel("策略名称", { exact: true })).toHaveValue("E2E 详细策略");
  await page.getByLabel("打标指令").fill("生成描述");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("E2E 假模型的打标结果", { exact: true }).last()).toBeVisible();

  const editor = page.getByRole("region", { name: "提示词编辑列" });
  const chat = page.getByRole("region", { name: "调试对话列" });
  const left = await editor.boundingBox();
  const right = await chat.boundingBox();
  if (!left || !right) throw new Error("工作台两栏不可见");
  expect(Math.abs(left.width - right.width)).toBeLessThanOrEqual(1);
  expect(left.y).toBe(right.y);
  expect(left.x + left.width).toBe(right.x);
  const body = page.getByLabel("正文（Markdown）");
  expect(await body.evaluate((element) => getComputedStyle(element).fontSize)).toBe("13px");
  expect(await body.evaluate((element) => getComputedStyle(element).lineHeight)).toBe("22.75px");
  await page.screenshot({ path: testInfo.outputPath("workbench-light.png"), fullPage: true });
  await page.getByRole("button", { name: /主题：/ }).click();
  await page.getByRole("button", { name: /主题：/ }).click();
  await page.screenshot({ path: testInfo.outputPath("workbench-dark.png"), fullPage: true });
  await page.getByRole("button", { name: "收起侧栏" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await expect(page.getByRole("button", { name: "切换提示词" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("workbench-mobile.png"), fullPage: true });
  expect(errors).toEqual([]);
});
