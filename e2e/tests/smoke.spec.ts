/**
 * 跨前后端 E2E：真实浏览器里的完整用户旅程。
 *
 * 与组件测试的分工：组件测试 mock 掉 api 层验证「交互 → 状态」；这里不 mock 任何
 * 东西，验证「浏览器 → HTTP → 后端 → 落盘 → 界面」整条链路。模型是同源假端点
 * （serving.py），链路真实性由 T17 系统测试背书。
 *
 * 注：页面主体暂为旧四页签组件嵌入新外壳的过渡形态（T25 / T26 按新信息架构逐页替换），
 * 旅程里的点击目标已对准新外壳的侧栏导航。
 */
import { expect, test } from "@playwright/test";

test.describe("界面冒烟", () => {
  test("移动导航的主题选择跨开关保留并与桌面及系统模式一致", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.getByRole("button", { name: "打开导航" }).click();
    const navigation = page.getByRole("dialog", { name: "导航" });
    await navigation.getByRole("button", { name: "主题：跟随系统，切换为亮色" }).click();
    await navigation.getByRole("button", { name: "主题：亮色，切换为暗色" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await navigation.getByRole("button", { name: "打标", exact: true }).click();
    await page.getByRole("button", { name: "打开导航" }).click();
    await expect(navigation.getByRole("button", { name: "主题：暗色，切换为跟随系统" })).toBeVisible();
    await navigation.getByRole("button", { name: "打标", exact: true }).click();
    await page.setViewportSize({ width: 1280, height: 720 });
    await expect(page.getByRole("button", { name: "主题：暗色，切换为跟随系统" })).toBeVisible();
    await page.emulateMedia({ colorScheme: "dark" });
    await page.emulateMedia({ colorScheme: "light" });
    await expect(page.locator("html")).toHaveClass(/dark/);
    await page.getByRole("button", { name: "主题：暗色，切换为跟随系统" }).click();
    await expect(page.locator("html")).not.toHaveClass(/dark/);
    await page.emulateMedia({ colorScheme: "dark" });
    await expect(page.locator("html")).toHaveClass(/dark/);
  });

  test("页面加载：品牌（副标题 + 版本）与工作区两项导航就位", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("sidebar").getByText("Dataset Factory", { exact: true })).toBeVisible();
    await expect(page.getByText("打标流水线工具")).toBeVisible();
    await expect(page.getByText("v0.1.0")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "策略", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "设置", exact: true }),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByTestId("sidebar")).not.toBeVisible();
    await page.getByRole("button", { name: "打开导航" }).click();
    const navigation = page.getByRole("dialog", { name: "导航" });
    await expect(navigation.getByText("Dataset Factory", { exact: true })).toBeVisible();
    await navigation.getByRole("button", { name: "打标", exact: true }).click();
    await expect(navigation).not.toBeVisible();
    await expect(page.getByRole("region", { name: "打标", exact: true })).toBeVisible();
  });
});

test.describe("提示词工作台", () => {
  test("新建提示词后保存并在下拉库中出现", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "切换提示词" }).click();
    await page.getByRole("button", { name: "新建提示词" }).click();

    await page.getByLabel("名称", { exact: true }).fill("e2e-prompt");
    await page.getByLabel("描述", { exact: true }).fill("E2E 建的条目");
    await page.getByLabel("正文（Markdown）").fill("你是打标助手（E2E）");
    await page.getByRole("button", { name: "保存", exact: true }).click();

    await expect(page.getByText("已保存提示词「e2e-prompt」")).toBeVisible();
    await expect(page.getByLabel("名称", { exact: true })).toHaveValue("e2e-prompt");
    await page.getByRole("button", { name: "切换提示词" }).click();
    await expect(
      page.getByRole("button", { name: "选择提示词 e2e-prompt", exact: true }),
    ).toBeVisible();
  });
});

test.describe("打标全链路", () => {
  test("发指令打标：假模型回复直达界面（浏览器 → HTTP → 引擎 → 假端点）", async ({ page }) => {
    // 先建一条基础提示词（打标请求要求已选定基础提示词）；保存后自动选中。
    await page.goto("/");
    await page.getByRole("button", { name: "切换提示词" }).click();
    await page.getByRole("button", { name: "新建提示词" }).click();
    await page.getByLabel("名称", { exact: true }).fill("e2e-label-prompt");
    await page.getByLabel("正文（Markdown）").fill("你是打标助手");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("已保存提示词「e2e-label-prompt」")).toBeVisible();

    // 发指令、等待假模型回复上屏（请求条里应带着刚建的基础提示词）。
    await page
      .getByLabel("打标指令")
      .fill("给这张图打个标");
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("E2E 假模型的打标结果")).toBeVisible({ timeout: 15_000 });
  });
});

test.describe("设置页", () => {
  test("端点配置详情：Base URL 与密钥来源正确显示（默认进连接子页）", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "设置", exact: true }).click();

    await expect(page.getByLabel("Base URL")).toHaveValue(/fake-llm\/v1$/);
    await expect(page.getByText(/已配置 · 来源：credentials 文件/)).toBeVisible();
    await expect(page.getByLabel("名称")).toBeDisabled();
  });
});
