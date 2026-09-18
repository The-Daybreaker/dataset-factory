import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EndpointConfigSummary, PromptInfo, SkillInfo } from "../../api";
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
  labelStream: vi.fn(),
  latestSession: vi.fn(),
  listStrategies: vi.fn(),
  listStrategyReferences: vi.fn(),
}));

vi.mock("../../api", () => {
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
    request_params: {},
  },
  {
    name: "backup",
    base_url: "https://b/v1",
    model: "model-b",
    api_format: "openai-chat-completions",
    has_api_key: false,
    is_active: false,
    request_params: {},
  },
];

const SKILLS: SkillInfo[] = [
  { name: "h3-skill", description: "H3 要求", enabled: true, body_chars: 860 },
];

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.listStrategies.mockResolvedValue([]);
  apiMock.listStrategyReferences.mockResolvedValue([]);
  apiMock.listPrompts.mockResolvedValue(PROMPTS);
  apiMock.listSkills.mockResolvedValue(SKILLS);
  apiMock.listEndpoints.mockResolvedValue(ENDPOINTS);
  apiMock.latestSession.mockRejectedValue(
    new (class MockNotFound extends Error {
      status = 404;
    })("no session"),
  );
  apiMock.getPrompt.mockResolvedValue(FULL_PROMPT);
  apiMock.labelStream.mockImplementation(async (_payload, handlers) => {
    handlers.onStart("s-1");
    handlers.onDelta("content", "打标结果 caption");
    handlers.onDone("s-1", "打标结果 caption");
  });
});

describe("PromptWorkbench", () => {
  it("连续选择附件时只采用最后一次读取结果，移除后不被迟到读取恢复", async () => {
    const readers: DeferredReader[] = [];
    class DeferredReader {
      result = "data:image/png;base64,AAAA";
      onload: (() => void) | null = null;
      readAsDataURL(): void {
        readers.push(this);
      }
    }
    vi.stubGlobal("FileReader", DeferredReader);
    try {
      render(<PromptWorkbench onNavigateToSettings={() => {}} />);
      await waitFor(() =>
        expect(screen.getByLabelText("名称")).toHaveValue("h3-video"),
      );

      for (const name of ["first.png", "second.png"]) {
        fireEvent.change(screen.getByLabelText("附图或视频"), {
          target: { files: [new File([name], name, { type: "image/png" })] },
        });
      }
      act(() => readers[1]?.onload?.());
      expect(screen.getByAltText("待打标图片 second.png")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "移除附件" }));
      act(() => readers[0]?.onload?.());

      expect(screen.queryByAltText(/待打标图片/)).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("端点激活期间禁止发送，失败后恢复原端点与输入", async () => {
    let rejectActivation!: (error: Error) => void;
    apiMock.activateEndpoint.mockReturnValueOnce(
      new Promise<void>((_resolve, reject) => {
        rejectActivation = reject;
      }),
    );
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("h3-video"));
    fireEvent.input(screen.getByLabelText("打标指令"), {
      target: { value: "保留指令" },
    });

    await userEvent.click(screen.getByLabelText("端点配置切换器"));
    await userEvent.click(screen.getByText("backup · model-b"));
    expect(screen.getByLabelText("端点配置切换器")).toBeDisabled();
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    fireEvent.keyDown(screen.getByLabelText("打标指令"), { key: "Enter" });
    expect(apiMock.labelStream).not.toHaveBeenCalled();
    await act(async () => rejectActivation(new Error("激活失败")));

    expect(screen.getByText(/激活失败/)).toBeInTheDocument();
    expect(screen.getByText("default · model-a")).toBeInTheDocument();
    expect(screen.getByLabelText("打标指令")).toHaveValue("保留指令");
    expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
  });

  it("选择 Skill 后迟到的会话恢复不替换当前组合", async () => {
    let resolveSession!: (value: unknown) => void;
    apiMock.latestSession.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSession = resolve;
      }),
    );
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("h3-video"));

    await userEvent.click(screen.getByRole("button", { name: "添加 Skill" }));
    await userEvent.click(screen.getByRole("menuitemcheckbox", { name: "h3-skill" }));
    await act(async () =>
      resolveSession({
        session_id: "old",
        settings: { prompt_name: "simple", skill_names: [] },
        messages: [{ role: "user", text: "旧对话", attachment: null }],
      }),
    );
    expect(screen.getByRole("menuitemcheckbox", { name: "h3-skill" })).toBeChecked();
    await userEvent.keyboard("{Escape}");

    expect(
      screen.getByRole("button", { name: "移除 Skill h3-skill" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("旧对话")).not.toBeInTheDocument();
    expect(apiMock.getPrompt).not.toHaveBeenCalledWith("simple");
  });

  it("开始输入指令后迟到的会话错误不打断当前对话", async () => {
    let rejectSession!: (error: Error) => void;
    apiMock.latestSession.mockReturnValueOnce(
      new Promise((_resolve, reject) => {
        rejectSession = reject;
      }),
    );
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("h3-video"));

    fireEvent.input(screen.getByLabelText("打标指令"), { target: { value: "新指令" } });
    await act(async () => rejectSession(new Error("旧会话读取失败")));

    expect(screen.queryByText(/旧会话读取失败/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("打标指令")).toHaveValue("新指令");
  });

  const strategy = {
    id: "a1",
    name: "备用策略",
    description: "",
    prompt: "simple",
    endpoint: "backup",
    skills: ["h3-skill"],
    available: true,
    missing_refs: [],
    created_at: "",
    updated_at: "",
  };

  it("提示词草稿锁定策略切换，保存后应用完整组合", async () => {
    apiMock.listStrategies.mockResolvedValue([strategy]);
    apiMock.savePrompt.mockResolvedValue(undefined);
    apiMock.activateEndpoint.mockResolvedValue(undefined);
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("h3-video"));

    fireEvent.input(screen.getByLabelText("描述"), { target: { value: "新描述" } });
    expect(screen.getByRole("button", { name: "切换策略" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "切换提示词" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "切换策略" })).toBeEnabled(),
    );
    apiMock.getPrompt.mockResolvedValue({
      name: "simple",
      description: "",
      body: "简短",
    });
    await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
    await userEvent.click(screen.getByRole("button", { name: "备用策略" }));

    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("simple"));
    expect(screen.getByText("backup · model-b")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "移除 Skill h3-skill" }),
    ).toBeInTheDocument();
  });

  it("策略端点激活失败保留原提示词与端点并允许重试", async () => {
    apiMock.listStrategies.mockResolvedValue([strategy]);
    apiMock.activateEndpoint.mockRejectedValueOnce(new Error("端点不可用"));
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("h3-video"));
    apiMock.getPrompt.mockResolvedValue({
      name: "simple",
      description: "",
      body: "简短",
    });

    await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
    await userEvent.click(screen.getByRole("button", { name: "备用策略" }));

    expect(await screen.findByText(/端点不可用/)).toBeInTheDocument();
    expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    expect(screen.getByText("default · model-a")).toBeInTheDocument();
  });

  it("迟到的会话恢复不覆盖已经编辑的提示词", async () => {
    let resolveSession!: (value: unknown) => void;
    apiMock.latestSession.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSession = resolve;
      }),
    );
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("h3-video"));

    fireEvent.input(screen.getByLabelText("描述"), { target: { value: "保留编辑" } });
    await act(async () =>
      resolveSession({
        session_id: "old",
        settings: { prompt_name: "simple", skill_names: ["h3-skill"] },
        messages: [],
      }),
    );

    expect(screen.getByLabelText("描述")).toHaveValue("保留编辑");
    expect(apiMock.getPrompt).not.toHaveBeenCalledWith("simple");
  });

  it("改名后写正文失败，保留草稿并按新名称重试保存", async () => {
    apiMock.renamePrompt.mockResolvedValue(undefined);
    apiMock.savePrompt
      .mockRejectedValueOnce(new Error("写入失败"))
      .mockResolvedValueOnce(undefined);
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("h3-video"));

    fireEvent.input(screen.getByLabelText("名称"), { target: { value: "renamed" } });
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/写入失败/)).toBeInTheDocument();
    expect(screen.getByLabelText("正文（Markdown）")).toHaveValue(FULL_PROMPT.body);
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("已保存提示词「renamed」")).toBeInTheDocument();
    expect(apiMock.renamePrompt).toHaveBeenCalledTimes(1);
    expect(apiMock.savePrompt).toHaveBeenLastCalledWith("renamed", {
      description: FULL_PROMPT.description,
      body: FULL_PROMPT.body,
    });
  });

  it("读取旧提示词迟到时不覆盖新建草稿", async () => {
    let resolvePrompt!: (value: typeof FULL_PROMPT) => void;
    apiMock.getPrompt.mockReturnValueOnce(
      new Promise<typeof FULL_PROMPT>((resolve) => {
        resolvePrompt = resolve;
      }),
    );
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);
    await waitFor(() => expect(apiMock.getPrompt).toHaveBeenCalledWith("h3-video"));

    await userEvent.click(screen.getByRole("button", { name: "切换提示词" }));
    await userEvent.click(screen.getByRole("button", { name: "新建提示词" }));
    fireEvent.input(screen.getByLabelText("名称"), { target: { value: "我的草稿" } });
    await act(async () => resolvePrompt(FULL_PROMPT));

    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("我的草稿"));
    expect(screen.getByLabelText("正文（Markdown）")).toHaveValue("");
  });

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
    expect(screen.getByRole("button", { name: "切换提示词" })).toBeEnabled();
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
    await userEvent.click(screen.getByRole("button", { name: "切换提示词" }));
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

  it("发送：labelStream 请求携带选中的基础提示词，流式渲染后上屏终稿与模型 meta", async () => {
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    await userEvent.type(screen.getByLabelText("打标指令"), "给这张图打个标");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(apiMock.labelStream).toHaveBeenCalledWith(
        expect.objectContaining({
          prompt_name: "h3-video",
          instruction: "给这张图打个标",
          skill_names: [],
        }),
        expect.objectContaining({
          onStart: expect.any(Function),
          onDelta: expect.any(Function),
          onDone: expect.any(Function),
          onError: expect.any(Function),
        }),
      );
    });
    expect(await screen.findByText("打标结果 caption")).toBeInTheDocument();
    expect(screen.getByText(/model-a · \d+s/)).toBeInTheDocument();
  });

  it("发送：流式增量在思考过程区与正文区逐段渲染，done 后上屏终稿", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    apiMock.labelStream.mockImplementation(async (_payload, handlers) => {
      handlers.onStart("s1");
      handlers.onDelta("reasoning", "先想想");
      handlers.onDelta("content", "打标结");
      await gate;
      handlers.onDelta("content", "果");
      handlers.onDone("s1", "打标结果");
    });
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    await userEvent.type(screen.getByLabelText("打标指令"), "给这张图打个标");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    // 流中：思考增量可见（details 内容在 DOM 中即算找到）、正文只到增量为止。
    await waitFor(() => {
      expect(screen.getByText("先想想")).toBeInTheDocument();
      expect(screen.getByText("打标结")).toBeInTheDocument();
    });

    release();
    // done：终稿上屏、生成中状态消失。
    expect(await screen.findByText("打标结果")).toBeInTheDocument();
    expect(screen.queryByText("生成中…")).not.toBeInTheDocument();
  });

  it("生成结束后思考过程保留在消息上，可展开回看（页面内存态）", async () => {
    apiMock.labelStream.mockImplementation(async (_payload, handlers) => {
      handlers.onStart("s1");
      handlers.onDelta("reasoning", "先想想构图");
      handlers.onDelta("content", "打标结果");
      handlers.onDone("s1", "打标结果");
    });
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    await userEvent.type(screen.getByLabelText("打标指令"), "给这张图打个标");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    // done 后：终稿上屏，思考区不再随流式面板一起消失，仍可展开回看。
    expect(await screen.findByText("打标结果")).toBeInTheDocument();
    expect(screen.queryByText("生成中…")).not.toBeInTheDocument();
    expect(screen.getByText("先想想构图")).toBeInTheDocument();
    expect(screen.getByText("思考过程")).toBeInTheDocument();
  });

  it("提示词库为空时发送：labelStream 收到 null（后端给可操作错误）", async () => {
    apiMock.listPrompts.mockResolvedValue([]);
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => expect(apiMock.listPrompts).toHaveBeenCalled());
    await userEvent.type(screen.getByLabelText("打标指令"), "打标");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(apiMock.labelStream).toHaveBeenCalledWith(
        expect.objectContaining({ prompt_name: null }),
        expect.anything(),
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
    await userEvent.click(screen.getByRole("button", { name: "切换提示词" }));
    await userEvent.click(screen.getByRole("button", { name: "删除提示词 h3-video" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(apiMock.deletePrompt).toHaveBeenCalledWith("h3-video");
    });
  });

  it("视频附件条：程序化向 fps / 帧上限输入框注入值不崩树，且值随请求发出（setState 更新器读 event 反模式回归）", async () => {
    // jsdom 的 FileReader 是异步的；换成同步回调的假件让 onload 立即触发。
    class FakeFileReader {
      result = "";
      onload: ((event: { target: FakeFileReader }) => void) | null = null;
      readAsDataURL(): void {
        this.result = "data:video/mp4;base64,AAAA";
        this.onload?.({ target: this });
      }
    }
    vi.stubGlobal("FileReader", FakeFileReader);
    apiMock.labelStream.mockImplementation(async (_payload, handlers) => {
      handlers.onStart("s1");
      handlers.onDelta("content", "视频描述");
      handlers.onDone("s1", "视频描述");
    });
    render(<PromptWorkbench onNavigateToSettings={() => {}} />);

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    fireEvent.change(screen.getByLabelText("附图或视频"), {
      target: {
        files: [new File(["fake-mp4"], "clip.mp4", { type: "video/mp4" })],
      },
    });

    // 2026-09-14 验收实测：此前对该输入框程序化注入值会让更新器读到已被置空的
    // event.currentTarget，抛 TypeError 崩掉整棵 React 树（白屏）。
    fireEvent.change(screen.getByLabelText("视频抽帧 fps"), {
      target: { value: "3" },
    });
    fireEvent.change(screen.getByLabelText("视频抽帧帧数上限"), {
      target: { value: "8" },
    });
    expect(screen.getByLabelText("视频抽帧 fps")).toHaveValue(3);
    expect(screen.getByLabelText("视频抽帧帧数上限")).toHaveValue(8);

    await userEvent.type(screen.getByLabelText("打标指令"), "描述视频");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(apiMock.labelStream).toHaveBeenCalledWith(
        expect.objectContaining({ video_fps: 3, video_max_frames: 8 }),
        expect.anything(),
      );
    });
    vi.unstubAllGlobals();
  });
});
