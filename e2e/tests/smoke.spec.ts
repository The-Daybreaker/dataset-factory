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
  test("页面加载：品牌、两组导航与流水线占位就位", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Dataset Factory")).toBeVisible();
    await expect(page.getByRole("button", { name: "提示词" })).toBeVisible();
    await expect(page.getByRole("button", { name: "设置" })).toBeVisible();
    await expect(page.getByRole("button", { name: "导入 / 素材库" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "导出" })).toBeDisabled();
  });
});

test.describe("提示词库旅程", () => {
  test("新建提示词 → 列表立即可见（后端写盘 + 界面刷新）", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "提示词" }).click();

    await page.getByLabel("名称").fill("e2e-prompt");
    await page.getByLabel(/描述/).fill("E2E 建的条目");
    await page.getByLabel(/正文/).fill("你是打标助手（E2E）");
    await page.getByRole("button", { name: "保存" }).click();

    await expect(page.getByText("已保存提示词「e2e-prompt」")).toBeVisible();
    // 精确匹配列表按钮（含描述）；模糊 /e2e-prompt/ 会同时命中「删除「e2e-prompt」」按钮。
    await expect(
      page.getByRole("button", { name: "e2e-prompt E2E 建的条目" }),
    ).toBeVisible();
  });
});

test.describe("打标全链路", () => {
  test("发指令打标：假模型回复直达界面（浏览器 → HTTP → 引擎 → 假端点）", async ({ page }) => {
    // 先建一条基础提示词（打标请求要求已选定基础提示词）。
    await page.goto("/");
    await page.getByRole("button", { name: "提示词" }).click();
    await page.getByLabel("名称").fill("e2e-label-prompt");
    await page.getByLabel(/正文/).fill("你是打标助手");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("已保存提示词「e2e-label-prompt」")).toBeVisible();

    // 过渡期提示词与对话同页：选提示词、发指令、等待假模型回复上屏。
    await page.getByLabel("基础提示词").selectOption("e2e-label-prompt");
    await page
      .getByPlaceholder("打标指令（如：给这张图打个标 / 改成两句话…）")
      .fill("给这张图打个标");
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText("E2E 假模型的打标结果")).toBeVisible({ timeout: 15_000 });
  });
});

test.describe("配置页", () => {
  test("加载配置：base_url 与密钥来源正确显示", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "设置" }).click();

    await expect(page.getByLabel("base_url")).toHaveValue(/fake-llm\/v1$/);
    await expect(page.getByText(/已配置（来源：credentials 文件）/)).toBeVisible();
  });
});
