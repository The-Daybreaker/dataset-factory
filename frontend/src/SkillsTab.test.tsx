/**
 * SkillsTab 组件测试：导入 / 启停切换两条主线。
 *
 * 启停的语义值得测：勾选 = 启用（打标注入全文），取消 = 停用。这是「停用不删除」
 * 这一产品决策在界面上的落点——如果有人误改成「取消即删除」，这条测试会红。
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SkillsTab } from "./SkillsTab";

const mocks = vi.hoisted(() => ({
  listSkills: vi.fn(),
  importSkill: vi.fn(),
  setSkillEnabled: vi.fn(),
  deleteSkill: vi.fn(),
}));

vi.mock("./api", () => ({
  api: {
    listSkills: mocks.listSkills,
    importSkill: mocks.importSkill,
    setSkillEnabled: mocks.setSkillEnabled,
    deleteSkill: mocks.deleteSkill,
  },
  errorMessage: (error: unknown) => String(error),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listSkills.mockResolvedValue([
    { name: "h3-prompt", description: "视频提示词规范", enabled: true },
  ]);
});

describe("SkillsTab", () => {
  it("导入成功：展示体积提示并刷新列表", async () => {
    mocks.importSkill.mockResolvedValue({
      name: "my-skill",
      description: "",
      enabled: true,
      total_bytes: 2048,
    });
    mocks.listSkills
      .mockResolvedValueOnce([{ name: "h3-prompt", description: "", enabled: true }])
      .mockResolvedValueOnce([
        { name: "h3-prompt", description: "", enabled: true },
        { name: "my-skill", description: "", enabled: true },
      ]);
    const user = userEvent.setup();
    render(<SkillsTab />);
    const pathInput = screen.getByPlaceholderText(/D:\/skills/);

    await user.type(pathInput, "D:/skills/my-skill");
    await user.click(screen.getByRole("button", { name: "导入" }));

    await waitFor(() => {
      expect(screen.getByText(/已导入「my-skill」/)).toBeInTheDocument();
    });
    expect(screen.getByText("my-skill")).toBeInTheDocument(); // 刷新后新条目可见
  });

  it("取消勾选：调用停用（而不是删除），并刷新列表", async () => {
    mocks.setSkillEnabled.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<SkillsTab />);
    const checkbox = await screen.findByRole("checkbox");

    await user.click(checkbox); // 启用 → 停用

    await waitFor(() => {
      expect(mocks.setSkillEnabled).toHaveBeenCalledWith("h3-prompt", false);
    });
    expect(mocks.deleteSkill).not.toHaveBeenCalled(); // 停用不删除——关键语义
  });
});
