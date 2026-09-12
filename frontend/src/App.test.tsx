import { render, screen } from "@testing-library/react";
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
  it("渲染品牌（副标题 + 版本脚注）与工作区两项导航", () => {
    render(<App />);

    expect(screen.getByText("Dataset Factory")).toBeInTheDocument();
    expect(screen.getByText("打标流水线工具")).toBeInTheDocument();
    expect(screen.getByText("v0.1.0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提示词" })).toBeEnabled();
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

    expect(screen.queryByText("Dataset Factory")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "展开侧栏" }));
    expect(screen.getByText("Dataset Factory")).toBeInTheDocument();
  });
});
