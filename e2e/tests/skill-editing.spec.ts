import { expect, test } from "@playwright/test";
import { isolatedBaseURL } from "./fixtures/isolated-servers";

// 本文件打真后端，用自己那份服务与数据根：导入的技能只落在自己的数据根里。
test.use({ baseURL: isolatedBaseURL("skill-editing.spec.ts") });

test("技能拖入、正文与描述写回经真实接口保存并刷新恢复", async ({ page, request }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "技能", exact: true }).click();
  await page.getByRole("button", { name: "导入 Skill" }).click();
  const content = "---\nname: e2e-caption\ndescription: old\nlicense: MIT\n---\n\nOriginal body\n";
  const transfer = await page.evaluateHandle((text) => {
    const data = new DataTransfer();
    data.items.add(new File([text], "SKILL.md", { type: "text/markdown" }));
    return data;
  }, content);
  await page.getByRole("button", { name: "拖入 Skill 包" }).dispatchEvent("drop", { dataTransfer: transfer });
  await expect(page.getByText(/已导入「e2e-caption」/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("skill-import.png") });
  await page.keyboard.press("Escape");
  const editor = page.getByLabel("技能文件内容");
  await expect(editor).toHaveValue(content);
  await editor.fill(`${content}Updated instructions\n`);
  await page.getByLabel("技能描述").fill("Updated description");
  await page.getByRole("button", { name: "保存更改" }).click();
  await expect(page.getByRole("button", { name: "保存更改" })).toBeDisabled();
  // 「保存更改」按钮变灰只代表前端认为写完了，落盘与登记是服务端另一回事：直接 GET 一次
  // 会在写可见之前读到旧内容（Linux runner 上实弹过一次）。改成轮询到内容落地再断言。
  let saved = "";
  await expect
    .poll(async () => {
      const result = await request.get("/api/skills/e2e-caption/files/SKILL.md");
      if (!result.ok()) return "";
      const body: { content?: string } = await result.json();
      saved = body.content ?? "";
      return saved.includes("Updated description") && saved.includes("Updated instructions");
    })
    .toBe(true);
  expect(saved).toContain("license: MIT");
  await expect(editor).toHaveValue(saved);
  await page.screenshot({ path: testInfo.outputPath("skill-light.png") });
  await page.getByRole("button", { name: /主题：/ }).click();
  await page.getByRole("button", { name: /主题：/ }).click();
  await page.screenshot({ path: testInfo.outputPath("skill-dark.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "打开导航" })).toBeVisible();
  const editorBounds = await editor.boundingBox();
  expect(editorBounds).not.toBeNull();
  expect(editorBounds!.width).toBeGreaterThan(280);
  expect(editorBounds!.x).toBeGreaterThanOrEqual(0);
  expect(editorBounds!.x + editorBounds!.width).toBeLessThanOrEqual(390);
  for (const name of ["导入 Skill", "保存更改", "放弃更改"]) {
    const bounds = await page.getByRole("button", { name, exact: true }).boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  }
  await page.screenshot({ path: testInfo.outputPath("skill-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "打开导航" }).click();
  const navigation = page.getByRole("dialog", { name: "导航" });
  await expect(navigation).toBeVisible();
  await navigation.getByRole("button", { name: "技能", exact: true }).click();
  await expect(navigation).not.toBeVisible();
  await expect(editor).toHaveValue(saved);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.reload();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "技能", exact: true }).click();
  await expect(editor).toHaveValue(saved);
  expect(errors).toEqual([]);
});
