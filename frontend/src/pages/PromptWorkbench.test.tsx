import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EndpointConfigSummary, PromptInfo, SkillInfo } from "../api";
import { PromptWorkbench } from "./PromptWorkbench";

// 工作台测试只关心「交互 → 调了哪个 API → 界面状态对不对」，api 层整体 mock 掉。
const apiMock = vi.hoisted(() => ({
  listPrompts: vi.fn(),
  getPrompt: vi.fn(),
  savePrompt: vi.fn(),
  renamePrompt: vi.fn(),
  deletePrompt: vi.fn(),
  listSkills: vi.fn(),
  listEndpoints: vi.fn(),
  activateEndpoint: vi.fn(),
  label: vi.fn(),
  latestSession: vi.fn(),
}));

vi.mock("../api", () => {
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
  return { api: apiMock, errorMessage: (error: unknown) => String(error), ApiError };
});

const PROMPTS: PromptInfo[] = [
  { name: "h3-video", description: "视频打标" },
  { name: "simple", description: "" },
];

const FULL_PROMPT = {
  name: "h3-video",
  description: "视频打标",
  body: "你是打标助手。",
};

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
  { name: "h3-skill", description: "H3 要求", enabled: true },
];

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.listPrompts.mockResolvedValue(PROMPTS);
  apiMock.listSkills.mockResolvedValue(SKILLS);
  apiMock.listEndpoints.mockResolvedValue(ENDPOINTS);
  apiMock.latestSession.mockRejectedValue(
    new (class MockNotFound extends Error {
      status = 404;
    })("no session"),
  );
  apiMock.getPrompt.mockResolvedValue(FULL_PROMPT);
  apiMock.label.mockResolvedValue({ caption: "打标结果 caption", session_id: "s-1" });
});

describe("PromptWorkbench", () => {
  it("进页拉取列表与端点配置；无会话恢复时自动选中首条作为本轮基础提示词", async () => {
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => {
      expect(apiMock.getPrompt).toHaveBeenCalledWith("h3-video");
    });
    // 端点切换器 chip 显示「名称 · 模型名」。
    expect(screen.getByText("default · model-a")).toBeInTheDocument();
    expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    expect(screen.getByLabelText("描述")).toHaveValue("视频打标");
    expect(screen.getByLabelText("正文（Markdown）")).toHaveValue("你是打标助手。");
    expect(screen.getByText("基础提示词：h3-video")).toBeInTheDocument();
  });

  it("改名保存：先 renamePrompt（改文件名）再按新名 savePrompt", async () => {
    apiMock.renamePrompt.mockResolvedValue(undefined);
    apiMock.savePrompt.mockResolvedValue(undefined);
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    await userEvent.clear(screen.getByLabelText("名称"));
    await userEvent.type(screen.getByLabelText("名称"), "h3-renamed");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(apiMock.renamePrompt).toHaveBeenCalledWith("h3-video", {
        new_name: "h3-renamed",
      });
    });
    await waitFor(() => {
      expect(apiMock.savePrompt).toHaveBeenCalledWith("h3-renamed", {
        description: "视频打标",
        body: "你是打标助手。",
      });
    });
  });

  it("保存：调 savePrompt（名称 + 描述 + 正文）并刷新列表", async () => {
    apiMock.savePrompt.mockResolvedValue(undefined);
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    await userEvent.click(screen.getByRole("button", { name: "新建提示词" }));
    await userEvent.type(screen.getByLabelText("名称"), "new-prompt");
    await userEvent.type(screen.getByLabelText("描述"), "新条目");
    await userEvent.type(screen.getByLabelText("正文（Markdown）"), "新的正文");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(apiMock.savePrompt).toHaveBeenCalledWith("new-prompt", {
        description: "新条目",
        body: "新的正文",
      });
    });
    expect(await screen.findByText("已保存提示词「new-prompt」")).toBeInTheDocument();
  });

  it("发送：label 请求携带选中的基础提示词，回复上屏并显示模型 meta", async () => {
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    await userEvent.type(screen.getByLabelText("打标指令"), "给这张图打个标");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(apiMock.label).toHaveBeenCalledWith(
        expect.objectContaining({
          prompt_name: "h3-video",
          instruction: "给这张图打个标",
          skill_names: [],
        }),
      );
    });
    expect(await screen.findByText("打标结果 caption")).toBeInTheDocument();
    expect(screen.getByText(/model-a · \d+s/)).toBeInTheDocument();
  });

  it("提示词库为空时发送：label 收到 null（后端给可操作错误）", async () => {
    apiMock.listPrompts.mockResolvedValue([]);
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() =>
      expect(
        screen.getByText("（提示词库为空——点「新建」写一条）"),
      ).toBeInTheDocument(),
    );
    await userEvent.type(screen.getByLabelText("打标指令"), "打标");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(apiMock.label).toHaveBeenCalledWith(
        expect.objectContaining({ prompt_name: null }),
      );
    });
  });

  it("端点切换器选择另一套配置：调 activateEndpoint 并更新 chip", async () => {
    apiMock.activateEndpoint.mockResolvedValue(undefined);
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => screen.getByText("default · model-a"));
    await userEvent.click(screen.getByLabelText("端点配置切换器"));
    await userEvent.click(screen.getByText("backup · model-b"));

    await waitFor(() => {
      expect(apiMock.activateEndpoint).toHaveBeenCalledWith("backup");
    });
    expect(await screen.findByText("backup · model-b")).toBeInTheDocument();
  });

  it("删除提示词：确认对话框 → 调 deletePrompt", async () => {
    apiMock.deletePrompt.mockResolvedValue(undefined);
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    // 编辑器列的「删除」打开确认对话框；对话框内的「删除」才真正调接口（危险动作二次确认）。
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(apiMock.deletePrompt).toHaveBeenCalledWith("h3-video");
    });
  });
});
