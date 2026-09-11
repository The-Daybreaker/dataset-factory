/**
 * ConfigTab 组件测试。
 *
 * 安全相关语义是这里的重点：密钥字段留空时**不发送** api_key（沿用已存密钥），
 * 填了才发送——「不强迫重输密钥」与「密钥不无故出前端」两个约定都要守住。
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfigTab } from "./ConfigTab";

const mocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  updateConfig: vi.fn(),
}));

vi.mock("./api", () => ({
  api: {
    getConfig: mocks.getConfig,
    updateConfig: mocks.updateConfig,
  },
  errorMessage: (error: unknown) => String(error),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getConfig.mockResolvedValue({
    base_url: "https://opencode.ai/zen/go/v1",
    model: "deepseek-v4-flash-vision-exp",
    api_key_configured: true,
    key_source: "file",
  });
});

describe("ConfigTab", () => {
  it("加载已有配置并标注密钥来源（不回显密钥内容——接口本来也不返回）", async () => {
    render(<ConfigTab />);

    expect(await screen.findByLabelText("base_url")).toHaveValue(
      "https://opencode.ai/zen/go/v1",
    );
    expect(
      screen.getByLabelText(/已配置（来源：credentials 文件）/),
    ).toBeInTheDocument();
  });

  it("密钥留空保存：只发 base_url / model，不发 api_key", async () => {
    mocks.updateConfig.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<ConfigTab />);
    await screen.findByLabelText("base_url");

    await user.clear(screen.getByLabelText("base_url"));
    await user.type(screen.getByLabelText("base_url"), "https://new.example.com/v1");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(screen.getByText("配置已保存")).toBeInTheDocument();
    });
    expect(mocks.updateConfig).toHaveBeenCalledWith({
      base_url: "https://new.example.com/v1",
      model: "deepseek-v4-flash-vision-exp",
    });
  });

  it("填写新密钥保存：api_key 一并发出", async () => {
    mocks.updateConfig.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<ConfigTab />);
    await screen.findByLabelText("base_url");

    await user.type(screen.getByLabelText(/API 密钥/), "sk-new-key");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(mocks.updateConfig).toHaveBeenCalledWith(
        expect.objectContaining({ api_key: "sk-new-key" }),
      );
    });
  });
});
