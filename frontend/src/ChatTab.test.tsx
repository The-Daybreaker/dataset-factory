/**
 * ChatTab 组件测试。
 *
 * 测试策略：mock 掉 api 模块（组件边界测试），只验证「用户操作 → 状态与展示」的
 * 对应关系，不碰网络。网络层的行为已由 api.test.ts 覆盖——分层测试的分工就体现在
 * 这里：改 API 封装不用动这些测试，改界面布局也不用动 api.test.ts。
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { ChatTab } from "./ChatTab";

const mocks = vi.hoisted(() => ({
  label: vi.fn(),
  listPrompts: vi.fn(),
  listSkills: vi.fn(),
  latestSession: vi.fn(),
}));

vi.mock("./api", () => ({
  api: {
    label: mocks.label,
    listPrompts: mocks.listPrompts,
    listSkills: mocks.listSkills,
    latestSession: mocks.latestSession,
  },
  errorMessage: (error: unknown) => String(error),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listPrompts.mockResolvedValue([
    { name: "base", description: "基础打标" },
  ]);
  mocks.listSkills.mockResolvedValue([]);
  mocks.latestSession.mockRejectedValue(new Error("还没有任何会话"));
});

describe("ChatTab", () => {
  it("发送成功：用户消息与 caption 都出现在对话里", async () => {
    mocks.label.mockResolvedValue({ session_id: "20260911-000000-000000", caption: "一只橘猫在晒太阳" });
    const user = userEvent.setup();
    render(<ChatTab />);

    await screen.findByPlaceholderText("打标指令（如：给这张图打个标 / 改成两句话…）");
    await user.type(
      screen.getByPlaceholderText("打标指令（如：给这张图打个标 / 改成两句话…）"),
      "给这张图打个标",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    // 发送的内容与模型回复都要展示（消息列表只追加）。
    await waitFor(() => {
      expect(screen.getByText("给这张图打个标")).toBeInTheDocument();
    });
    expect(screen.getByText("一只橘猫在晒太阳")).toBeInTheDocument();
    // 调用契约：指令文本原样传给 api.label。
    expect(mocks.label).toHaveBeenCalledWith(
      expect.objectContaining({ instruction: "给这张图打个标", prompt_name: null }),
    );
  });

  it("指令为空且无图时发送按钮禁用（防误触空轮）", async () => {
    render(<ChatTab />);
    await screen.findByText("base"); // 等列表加载完，避免异步 setState 干扰

    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    expect(mocks.label).not.toHaveBeenCalled();
  });

  it("发送失败：错误消息展示且输入保留（不丢用户输入）", async () => {
    mocks.label.mockRejectedValue(new Error("模型端点调用失败（请求 id: abc123，可拿它对后端日志）"));
    const user = userEvent.setup();
    render(<ChatTab />);
    const input = screen.getByPlaceholderText("打标指令（如：给这张图打个标 / 改成两句话…）");

    await user.type(input, "给这张图打个标");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.getByText(/请求 id: abc123/)).toBeInTheDocument();
    });
    expect(input).toHaveValue("给这张图打个标");
  });

  it("发送中：按钮显示等待进度并禁用（防重复提交）", async () => {
    // 手动控制 promise 的 resolve 时机，才能观察到「发送中」的中间态。
    let resolveLabel: (value: { session_id: string; caption: string }) => void = () => {};
    mocks.label.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveLabel = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<ChatTab />);
    const input = screen.getByPlaceholderText("打标指令（如：给这张图打个标 / 改成两句话…）");

    await user.type(input, "打标");
    await user.click(screen.getByRole("button", { name: "发送" }));

    // 中间态：按钮变成等待文案且不可再点。
    const waiting = await screen.findByRole("button", { name: /已发送，等待模型/ });
    expect(waiting).toBeDisabled();

    resolveLabel({ session_id: "s1", caption: "完成" });
    await screen.findByText("完成");
  });
});
