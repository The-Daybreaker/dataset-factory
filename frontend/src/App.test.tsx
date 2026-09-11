import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";

// App 默认渲染聊天页，而它进页面就要拉列表并恢复会话。这里把 API 层整体换掉，
// 让这条测试只关心「标题与页签渲染得对不对」，不碰网络（更细的交互测试见 T18）。
vi.mock("./api", () => {
  // ChatTab 依赖 ApiError（按 status 404 判定「还没有会话」的正常空态），mock 模块需提供该类。
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
      latestSession: vi
        .fn()
        .mockRejectedValue(
          new ApiError("http", "还没有任何会话；发第一轮打标即自动创建。", 404, null),
        ),
    },
    errorMessage: (error: unknown) => String(error),
    ApiError,
  };
});

describe("App", () => {
  it("渲染应用标题与四个页签", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "Dataset Factory 打标测试台" }),
    ).toBeInTheDocument();
    for (const label of ["聊天打标", "提示词库", "Skill", "配置"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });
});
