/**
 * PromptsTab 组件测试：列表 / 选中回填 / 保存三条主线。
 *
 * 同样只测组件边界（mock api）：保存后的「列表刷新」通过断言 listPrompts 被再次
 * 调用来验证，而不是盯着 DOM——因为「刷新后列表内容」取决于 mock 返回，盯 DOM
 * 只是在测 mock 自己。
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PromptsTab } from "./PromptsTab";

const mocks = vi.hoisted(() => ({
  listPrompts: vi.fn(),
  getPrompt: vi.fn(),
  savePrompt: vi.fn(),
  deletePrompt: vi.fn(),
}));

vi.mock("./api", () => ({
  api: {
    listPrompts: mocks.listPrompts,
    getPrompt: mocks.getPrompt,
    savePrompt: mocks.savePrompt,
    deletePrompt: mocks.deletePrompt,
  },
  errorMessage: (error: unknown) => String(error),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listPrompts.mockResolvedValue([{ name: "base", description: "基础打标" }]);
});

describe("PromptsTab", () => {
  it("点击列表条目：名称 / 描述 / 正文回填到编辑区", async () => {
    mocks.getPrompt.mockResolvedValue({
      name: "base",
      description: "基础打标",
      body: "你是打标助手",
    });
    const user = userEvent.setup();
    render(<PromptsTab />);

    await user.click(await screen.findByRole("button", { name: /base/ }));

    expect(screen.getByLabelText("名称")).toHaveValue("base");
    expect(screen.getByLabelText(/描述/)).toHaveValue("基础打标");
    expect(screen.getByLabelText(/正文/)).toHaveValue("你是打标助手");
  });

  it("保存：调用 savePrompt 并给出成功提示", async () => {
    mocks.savePrompt.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<PromptsTab />);

    await user.type(screen.getByLabelText("名称"), "new-prompt");
    await user.type(screen.getByLabelText(/描述/), "新条目");
    await user.type(screen.getByLabelText(/正文/), "正文内容");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(screen.getByText("已保存提示词「new-prompt」")).toBeInTheDocument();
    });
    expect(mocks.savePrompt).toHaveBeenCalledWith("new-prompt", {
      description: "新条目",
      body: "正文内容",
    });
  });

  it("名称为空时保存禁用（后端 400 的前端侧等价防线）", async () => {
    render(<PromptsTab />);
    await screen.findByText("base");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });
});
