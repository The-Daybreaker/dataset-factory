import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EndpointConfigSummary, SkillInfo } from "../api";
import { SettingsPage } from "./SettingsPage";

// 设置页测试只关心「交互 → 调了哪个 API → 界面状态对不对」，api 层整体 mock 掉。
// 注意查询唯一性：选中的名称同时出现在列表行与详情标题里，断言一律用 label / 唯一文本。
const apiMock = vi.hoisted(() => ({
  listEndpoints: vi.fn(),
  createEndpoint: vi.fn(),
  updateEndpoint: vi.fn(),
  deleteEndpoint: vi.fn(),
  activateEndpoint: vi.fn(),
  listSkills: vi.fn(),
  importSkill: vi.fn(),
  setSkillEnabled: vi.fn(),
  deleteSkill: vi.fn(),
  listSkillFiles: vi.fn(),
  readSkillFile: vi.fn(),
}));

vi.mock("../api", () => ({
  api: apiMock,
  errorMessage: (error: unknown) => String(error),
}));

const ENDPOINTS: EndpointConfigSummary[] = [
  {
    name: "default",
    base_url: "https://a/v1",
    model: "model-a",
    api_format: "openai-chat-completions",
    has_api_key: true,
    is_active: true,
  },
  {
    name: "backup",
    base_url: "https://b/v1",
    model: "model-b",
    api_format: "openai-chat-completions",
    has_api_key: false,
    is_active: false,
  },
];

const SKILLS: SkillInfo[] = [
  { name: "h3-skill", description: "H3 官方要求", enabled: true },
  { name: "old-skill", description: "", enabled: false },
];

const SKILL_FILES = {
  name: "h3-skill",
  files: [
    { path: "SKILL.md", role: "skill", previewable: true },
    { path: "references/detail.md", role: "reference", previewable: true },
    { path: "assets/cover.png", role: "asset", previewable: false },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.listEndpoints.mockResolvedValue(ENDPOINTS);
  apiMock.listSkills.mockResolvedValue(SKILLS);
  apiMock.listSkillFiles.mockResolvedValue(SKILL_FILES);
  apiMock.readSkillFile.mockResolvedValue({
    path: "SKILL.md",
    content: "# Example\n按格式输出 caption。",
  });
});

describe("SettingsPage · 连接·端点配置", () => {
  it("列表 + 详情回填：名称只读、密钥只报来源不回内容", async () => {
    render(<SettingsPage />);

    await waitFor(() => {
      expect(screen.getByLabelText("Base URL")).toHaveValue("https://a/v1");
    });
    expect(screen.getByText("backup")).toBeInTheDocument();
    expect(screen.getByLabelText("名称")).toHaveValue("default");
    expect(screen.getByLabelText("名称")).toBeDisabled();
    expect(screen.getByLabelText("模型名")).toHaveValue("model-a");
    expect(screen.getByText(/已配置 · 来源：credentials 文件/)).toBeInTheDocument();
  });

  it("保存更改：不带密钥调 updateEndpoint（后端沿用已存密钥）", async () => {
    apiMock.updateEndpoint.mockResolvedValue(ENDPOINTS[0]);
    render(<SettingsPage />);

    await waitFor(() => screen.getByLabelText("Base URL"));
    await userEvent.clear(screen.getByLabelText("模型名"));
    await userEvent.type(screen.getByLabelText("模型名"), "model-a2");
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));

    await waitFor(() => {
      expect(apiMock.updateEndpoint).toHaveBeenCalledWith("default", {
        base_url: "https://a/v1",
        model: "model-a2",
        api_format: "openai-chat-completions",
      });
    });
    expect(await screen.findByText("已保存「default」的更改")).toBeInTheDocument();
  });

  it("添加配置：创建后给出成功反馈", async () => {
    const created: EndpointConfigSummary = {
      name: "new-one",
      base_url: "https://n/v1",
      model: "m",
      api_format: "openai-chat-completions",
      has_api_key: true,
      is_active: false,
    };
    // 真实后端在创建后会把它返回进列表；mock 同样按两次调用给不同结果，
    // 否则创建后 reload 拿到不含新配置的列表，详情区退回占位、反馈条被卸载。
    apiMock.listEndpoints
      .mockResolvedValueOnce(ENDPOINTS)
      .mockResolvedValue([...ENDPOINTS, created]);
    apiMock.createEndpoint.mockResolvedValue(created);
    render(<SettingsPage />);

    await waitFor(() => screen.getByLabelText("Base URL"));
    await userEvent.click(screen.getByRole("button", { name: "添加配置" }));
    await userEvent.type(screen.getByLabelText("名称"), "new-one");
    await userEvent.type(screen.getByLabelText("Base URL"), "https://n/v1");
    await userEvent.type(screen.getByLabelText("模型名"), "m");
    await userEvent.type(screen.getByLabelText("API 密钥"), "sk-new-key"); // pragma: allowlist secret —— 测试假密钥
    await userEvent.click(screen.getByRole("button", { name: "创建配置" }));

    await waitFor(() => {
      expect(apiMock.createEndpoint).toHaveBeenCalledWith({
        name: "new-one",
        base_url: "https://n/v1",
        model: "m",
        api_format: "openai-chat-completions",
        api_key: "sk-new-key", // pragma: allowlist secret —— 测试假密钥
      });
    });
    expect(await screen.findByText("已创建配置「new-one」")).toBeInTheDocument();
  });

  it("未激活配置显示「设为当前使用」；点击调 activateEndpoint", async () => {
    apiMock.activateEndpoint.mockResolvedValue(undefined);
    render(<SettingsPage />);

    await waitFor(() => screen.getByText("backup"));
    await userEvent.click(screen.getByText("backup"));
    await userEvent.click(await screen.findByRole("button", { name: "设为当前使用" }));

    await waitFor(() => {
      expect(apiMock.activateEndpoint).toHaveBeenCalledWith("backup");
    });
  });

  it("删除：确认对话框内的删除钮才真正调 deleteEndpoint", async () => {
    apiMock.deleteEndpoint.mockResolvedValue(undefined);
    render(<SettingsPage />);

    await waitFor(() => screen.getByLabelText("Base URL"));
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(apiMock.deleteEndpoint).toHaveBeenCalledWith("default");
    });
  });
});

describe("SettingsPage · 能力·技能", () => {
  async function openSkills(): Promise<void> {
    render(<SettingsPage />);
    await waitFor(() => screen.getByLabelText("Base URL"));
    await userEvent.click(screen.getByRole("button", { name: "技能" }));
    await waitFor(() => screen.getByLabelText("启用 h3-skill"));
  }

  it("列表行：状态开关 + 状态徽章；点开关调 setSkillEnabled（停用 ≠ 删除）", async () => {
    await openSkills();

    expect(screen.getAllByText("已启用").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已停用").length).toBeGreaterThan(0);

    await userEvent.click(screen.getByLabelText("启用 h3-skill"));

    await waitFor(() => {
      expect(apiMock.setSkillEnabled).toHaveBeenCalledWith("h3-skill", false);
    });
  });

  it("搜索框按名称过滤列表", async () => {
    await openSkills();

    await userEvent.type(screen.getByLabelText("搜索技能"), "old");

    expect(screen.getByLabelText("启用 old-skill")).toBeInTheDocument();
    expect(screen.queryByLabelText("启用 h3-skill")).not.toBeInTheDocument();
  });

  it("导入成功：调 importSkill 并给出体积与 token 提醒反馈条", async () => {
    apiMock.importSkill.mockResolvedValue({
      name: "fresh",
      description: "",
      enabled: true,
      total_bytes: 42_000,
    });
    await openSkills();

    await userEvent.type(screen.getByLabelText("Skill 包路径"), "D:/skills/fresh");
    await userEvent.click(screen.getByRole("button", { name: "导入" }));

    await waitFor(() => {
      expect(apiMock.importSkill).toHaveBeenCalledWith("D:/skills/fresh");
    });
    expect(
      await screen.findByText(/41\.0 KiB——skill 全文将注入打标请求/),
    ).toBeInTheDocument();
  });

  it("包文件 chips：可预览文件点击加载内容；assets 灰显禁用（不参与注入）", async () => {
    await openSkills();

    // 默认预览注入源 SKILL.md（chip 与预览框标题都可能有它，断言内容文本即可）。
    await waitFor(() => {
      expect(screen.getByText(/按格式输出 caption/)).toBeInTheDocument();
    });
    expect(screen.getByText("references/detail.md")).toBeEnabled();
    expect(screen.getByText("assets/cover.png")).toBeDisabled();

    await userEvent.click(screen.getByText("references/detail.md"));

    await waitFor(() => {
      expect(apiMock.readSkillFile).toHaveBeenCalledWith(
        "h3-skill",
        "references/detail.md",
      );
    });
  });
});
