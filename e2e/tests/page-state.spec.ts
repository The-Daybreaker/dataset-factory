/**
 * 页面状态保持 E2E（三期）：切页往返零重置 + reload 模拟重启。
 *
 * 与组件测试的分工：组件测试在 jsdom 里验证「机制」（Activity 保活、镜像优先级、
 * 失效回退）；这里在真实浏览器里验证「体验」——编辑草稿、对话输入、打标筛选词
 * 在切页与重启后确实还在，隐藏页以 display:none 常驻 DOM，重启直达上次页面。
 */
import { expect, test } from "@playwright/test";
import { isolatedBaseURL } from "./fixtures/isolated-servers";

// 本文件打真后端，用自己那份服务与数据根（隔离口径与其余 spec 相同）。
test.use({ baseURL: isolatedBaseURL("page-state.spec.ts") });

test.describe("页面状态保持（三期）", () => {
  test("编辑草稿与对话输入跨切页保留，切走的页隐藏常驻 DOM", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("名称", { exact: true }).fill("跨页草稿");
    await page.getByLabel("描述", { exact: true }).fill("切页不应丢我");
    await page.getByLabel("打标指令").fill("切页还在的一句话");

    await page.getByRole("button", { name: "打标", exact: true }).click();
    await expect(page.getByTestId("page-labeling")).toBeVisible();
    // 保活的直接证据：切走后策略页仍 attached（display:none），不再是卸载重挂。
    await expect(page.getByTestId("page-prompts")).toBeAttached();
    await expect(page.getByTestId("page-prompts")).not.toBeVisible();

    await page.getByRole("button", { name: "策略", exact: true }).click();
    await expect(page.getByLabel("名称", { exact: true })).toHaveValue("跨页草稿");
    await expect(page.getByLabel("描述", { exact: true })).toHaveValue("切页不应丢我");
    await expect(page.getByLabel("打标指令")).toHaveValue("切页还在的一句话");
  });

  test("打标页筛选词跨切页保留", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "打标", exact: true }).click();
    await expect(page.getByTestId("page-labeling")).toBeVisible();
    await page.getByPlaceholder("搜索条目").fill("过滤词");

    await page.getByRole("button", { name: "策略", exact: true }).click();
    await page.getByRole("button", { name: "打标", exact: true }).click();

    await expect(page.getByPlaceholder("搜索条目")).toHaveValue("过滤词");
  });

  test("reload 模拟重启：直达上次页面，筛选词与编辑草稿恢复", async ({ page }) => {
    await page.goto("/");
    // 先在策略页留一份编辑器草稿（进镜像），再切到打标页留下筛选词与「上次页面」。
    await page.getByLabel("名称", { exact: true }).fill("重启草稿");
    await page.getByRole("button", { name: "打标", exact: true }).click();
    await expect(page.getByTestId("page-labeling")).toBeVisible();
    await page.getByPlaceholder("搜索条目").fill("重启后还在");

    await page.reload();

    // 重启直达上次停留的页面（打标），筛选词从 localStorage 恢复。
    await expect(page.getByTestId("page-labeling")).toBeVisible();
    await expect(page.getByPlaceholder("搜索条目")).toHaveValue("重启后还在");

    // 首次进入策略页：编辑器从镜像恢复（重启前写下的草稿原样回来）。
    await page.getByRole("button", { name: "策略", exact: true }).click();
    await expect(page.getByTestId("page-prompts")).toBeVisible();
    await expect(page.getByLabel("名称", { exact: true })).toHaveValue("重启草稿");
  });

  test("策略会话跨重启：策略选中恢复、对话接续、切走再切回不丢（v3）", async ({ page }) => {
    await page.goto("/");
    // 建提示词 → 存 → 建策略 → 存 → 下拉点选应用 → 发消息等回复。
    await page.getByRole("button", { name: "切换提示词" }).click();
    await page.getByRole("button", { name: "新建提示词" }).click();
    await page.getByLabel("名称", { exact: true }).fill("状态保持策略提示词");
    await page.getByLabel("正文（Markdown）").fill("客观描述可见画面。");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText(/已保存提示词/)).toBeVisible();
    await page.getByLabel("策略名称", { exact: true }).fill("E2E 状态策略");
    await page.getByRole("button", { name: "保存策略" }).click();
    await page.getByRole("button", { name: "切换策略" }).click();
    await page.getByRole("button", { name: /^E2E 状态策略/ }).click();
    await page.getByLabel("打标指令").fill("策略会话第一句");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByText("E2E 假模型的打标结果").last()).toBeVisible({
      timeout: 15_000,
    });

    await page.reload();

    // 用户实测场景：重启后策略仍是「E2E 状态策略」，对话历史挂在它下面。
    await expect(page.getByLabel("策略名称", { exact: true })).toHaveValue(
      "E2E 状态策略",
    );
    await expect(page.getByText("E2E 假模型的打标结果").last()).toBeVisible();

    // 切走（新建策略 = 进草稿桶）→ 草稿桶还没有会话，空白。
    await page.getByRole("button", { name: "切换策略" }).click();
    await page.getByRole("button", { name: "新建策略" }).click();
    await expect(page.getByText(/暂无消息/)).toBeVisible();

    // 切回 → 进该策略的桶接续它的最近会话，历史回来。
    await page.getByRole("button", { name: "切换策略" }).click();
    await page.getByRole("button", { name: /^E2E 状态策略/ }).click();
    await expect(page.getByText("E2E 假模型的打标结果").last()).toBeVisible();
  });

  test("每策略各自的最近会话；新建策略的对话不错绑到已有策略（v3）", async ({ page }) => {
    await page.goto("/");
    // 建提示词 → 两条策略共用它（共用提示词正是 v2 签名错绑的温床）。
    await page.getByRole("button", { name: "切换提示词" }).click();
    await page.getByRole("button", { name: "新建提示词" }).click();
    await page.getByLabel("名称", { exact: true }).fill("归属隔离提示词");
    await page.getByLabel("正文（Markdown）").fill("客观描述可见画面。");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText(/已保存提示词/)).toBeVisible();

    // 策略 A：保存 → 应用 → 发言。
    await page.getByLabel("策略名称", { exact: true }).fill("桶隔离A");
    await page.getByRole("button", { name: "保存策略" }).click();
    await page.getByRole("button", { name: "切换策略" }).click();
    await page.getByRole("button", { name: /^桶隔离A/ }).click();
    await page.getByLabel("打标指令").fill("A 的第一句");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByText("E2E 假模型的打标结果").last()).toBeVisible({
      timeout: 15_000,
    });

    // 策略 B：新建（进草稿桶）→ 保存 → 应用 → 发言。
    await page.getByRole("button", { name: "切换策略" }).click();
    await page.getByRole("button", { name: "新建策略" }).click();
    await page.getByLabel("策略名称", { exact: true }).fill("桶隔离B");
    await page.getByRole("button", { name: "保存策略" }).click();
    await page.getByRole("button", { name: "切换策略" }).click();
    await page.getByRole("button", { name: /^桶隔离B/ }).click();
    await page.getByLabel("打标指令").fill("B 的第一句");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByText("E2E 假模型的打标结果").last()).toBeVisible({
      timeout: 15_000,
    });

    // 切回 A：接的是 A 桶的历史，B 的发言不串场（v2 签名错绑的回归点）。
    await page.getByRole("button", { name: "切换策略" }).click();
    await page.getByRole("button", { name: /^桶隔离A/ }).click();
    await expect(page.getByText("A 的第一句")).toBeVisible();
    await expect(page.getByText("B 的第一句")).not.toBeVisible();

    // 新建策略聊一句 → 草稿桶会话；切回 A 时它不错绑到 A。
    await page.getByRole("button", { name: "切换策略" }).click();
    await page.getByRole("button", { name: "新建策略" }).click();
    await page.getByLabel("打标指令").fill("草稿桶的一句话");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByText("E2E 假模型的打标结果").last()).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: "切换策略" }).click();
    await page.getByRole("button", { name: /^桶隔离A/ }).click();
    await expect(page.getByText("A 的第一句")).toBeVisible();
    await expect(page.getByText("草稿桶的一句话")).not.toBeVisible();

    // 重启：草稿桶会话在新建态下接续（新建策略点击 = 接续草稿桶）。
    await page.reload();
    await page.getByRole("button", { name: "切换策略" }).click();
    await page.getByRole("button", { name: "新建策略" }).click();
    await expect(page.getByText("草稿桶的一句话")).toBeVisible();
  });
});
