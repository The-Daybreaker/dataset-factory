import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

// App 渲染外壳并默认进提示词页（旧组件过渡期会拉列表与恢复会话）。这里把 API 层整体
// 换掉，让测试只关心「外壳与导航渲染得对不对」，不碰网络（页面级交互测试随后续页面重构补）。
vi.mock("./api", () => {
  // 过渡期嵌入的 ChatTab 依赖 ApiError（按 status 404 判定「还没有会话」的正常空态）。
  class ApiError extends Error {
    status: number | null;
    kind: string;
    requestId: string | null;
    constructor(
      kind: string,
      message: string,
      status: number | null,
      requestId: string | null,
    ) {
      super(message);
      this.kind = kind;
      this.status = status;
      this.requestId = requestId;
    }
  }
  return {
    api: {
      listStrategies: vi.fn().mockResolvedValue([]),
      listPrompts: vi.fn().mockResolvedValue([]),
      listSkills: vi.fn().mockResolvedValue([]),
      listEndpoints: vi.fn().mockResolvedValue([]),
      latestSession: vi
        .fn()
        .mockRejectedValue(
          new ApiError("http", "还没有任何会话；发第一轮打标即自动创建。", 404, null),
        ),
      getConfig: vi.fn().mockResolvedValue({
        name: null,
        base_url: null,
        model: null,
        api_key_configured: false,
        key_source: null,
      }),
    },
    errorMessage: (error: unknown) => String(error),
    ApiError,
  };
});

describe("App 外壳", () => {
  it("设置侧栏切换子页并返回进入前的工作页", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "设置" }));
    await user.click(screen.getByRole("button", { name: "技能" }));

    // 设置页现在是进页面才载的分包：先等它出现，再按原样断言（断言对象与强度不变）。
    await screen.findByRole("heading", { name: "技能", level: 1 });
    expect(screen.getByRole("heading", { name: "技能", level: 1 })).toBeVisible();
    // 「设置页不加二级导航地标」得是可证伪的断言：数当前有几个导航地标
    // （只有侧栏那一个），多加一个就会红——原来查一个根本不存在的名字必然永真。
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "返回工作区" }));
    expect(screen.getByRole("button", { name: "策略" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await user.click(screen.getByRole("button", { name: "切换提示词" }));
    expect(screen.getByRole("button", { name: "新建提示词" })).toBeVisible();
  });

  it("渲染品牌（副标题 + 版本脚注）与工作区两项导航", () => {
    render(<App />);

    expect(
      within(screen.getByTestId("sidebar")).getByText("Dataset Factory"),
    ).toBeInTheDocument();
    expect(screen.getByText("打标流水线工具")).toBeInTheDocument();
    expect(screen.getByText("v0.1.0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "策略" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "设置" })).toBeEnabled();
  });

  it("点击导航切换页面：设置页出现旧配置面板（过渡期嵌入）", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "设置" }));

    expect(screen.getByRole("button", { name: "设置" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("侧栏可折叠：收起后品牌文字隐藏、宽度收窄", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "收起侧栏" }));

    expect(
      within(screen.getByTestId("sidebar")).queryByText("Dataset Factory"),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "展开侧栏" }));
    expect(
      within(screen.getByTestId("sidebar")).getByText("Dataset Factory"),
    ).toBeInTheDocument();
  });
});
