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
import {
  ApiError,
  type EndpointConfigSummary,
  type PromptInfo,
  type SkillInfo,
} from "../../api";
import { ChatSessionProvider } from "./chat-session";
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
}));

// 部分 mock：只替掉 api 对象，ApiError / errorMessage 用真货——错误分档要靠真类的 kind 字段判。
vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: apiMock,
}));

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
  // 三期起编辑器状态会镜像落盘、外壳状态会持久化：清档防止用例间的「重启」串状态。
  localStorage.clear();
  apiMock.listStrategies.mockResolvedValue([]);
  apiMock.listPrompts.mockResolvedValue(PROMPTS);
  apiMock.listSkills.mockResolvedValue(SKILLS);
  apiMock.listEndpoints.mockResolvedValue(ENDPOINTS);
  // 「还没有会话」是 404，走真 ApiError（界面按 status 判它是首次使用的正常空态）。
  apiMock.latestSession.mockRejectedValue(
    new ApiError("http", "还没有任何会话；发第一轮打标即自动创建。", 404, null),
  );
  apiMock.getPrompt.mockResolvedValue(FULL_PROMPT);
  apiMock.labelStream.mockImplementation(async (_payload, handlers) => {
    handlers.onStart("s-1");
    handlers.onDelta("content", "打标结果 caption");
    handlers.onDone("s-1", "打标结果 caption");
  });
});

/** 工作台的对话状态住在 App 级会话域里：渲染必须包 Provider（与 App 的真实装配一致）。 */
const renderWorkbench = () =>
  render(
    <ChatSessionProvider>
      <PromptWorkbench onNavigateToSettings={() => {}} />
    </ChatSessionProvider>,
  );

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
      renderWorkbench();
      await waitFor(() =>
        expect(screen.getByLabelText("名称")).toHaveValue("h3-video"),
      );

      for (const name of ["first.png", "second.png"]) {
        fireEvent.change(screen.getByLabelText("附图或视频（最多 1 个）"), {
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
    renderWorkbench();
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
    renderWorkbench();
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

  it("历史恢复的助手消息带思考过程：折叠区跟着回来", async () => {
    apiMock.latestSession.mockResolvedValueOnce({
      session_id: "s-old",
      settings: { prompt_name: "h3-video", skill_names: ["h3-skill"] },
      messages: [
        { role: "user", text: "给这张图打个标", attachment: null },
        {
          role: "assistant",
          text: "一个穿红外套的人在雪地里",
          attachment: null,
          reasoning: "先确认主体与服装，再补构图。",
        },
      ],
    });

    renderWorkbench();

    expect(await screen.findByText("一个穿红外套的人在雪地里")).toBeInTheDocument();
    fireEvent.click(screen.getByText("思考过程"));
    expect(screen.getByText("先确认主体与服装，再补构图。")).toBeInTheDocument();
  });

  it("开始输入指令后迟到的会话错误不打断当前对话", async () => {
    let rejectSession!: (error: Error) => void;
    apiMock.latestSession.mockReturnValueOnce(
      new Promise((_resolve, reject) => {
        rejectSession = reject;
      }),
    );
    renderWorkbench();
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
    body_chars: 1200,
    available: true,
    missing_refs: [],
    created_at: "",
    updated_at: "",
  };

  it("提示词草稿锁定策略切换，保存后应用完整组合", async () => {
    apiMock.listStrategies.mockResolvedValue([strategy]);
    apiMock.savePrompt.mockResolvedValue(undefined);
    apiMock.activateEndpoint.mockResolvedValue(undefined);
    renderWorkbench();
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("h3-video"));

    fireEvent.input(screen.getByLabelText("描述"), { target: { value: "新描述" } });
    // L9：策略切换锁定改为「列表照开、点了才提示」（StrategyToolbar 用例覆盖）；
    // 这里只保留提示词切换钮的禁用断言。
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
    await userEvent.click(screen.getByRole("button", { name: /^备用策略/ }));

    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("simple"));
    expect(screen.getByText("backup · model-b")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "移除 Skill h3-skill" }),
    ).toBeInTheDocument();
  });

  it("策略端点激活失败保留原提示词与端点并允许重试", async () => {
    apiMock.listStrategies.mockResolvedValue([strategy]);
    apiMock.activateEndpoint.mockRejectedValueOnce(new Error("端点不可用"));
    renderWorkbench();
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("h3-video"));
    apiMock.getPrompt.mockResolvedValue({
      name: "simple",
      description: "",
      body: "简短",
    });

    await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
    await userEvent.click(screen.getByRole("button", { name: /^备用策略/ }));

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
    renderWorkbench();
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
    renderWorkbench();
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
    renderWorkbench();
    await waitFor(() => expect(apiMock.getPrompt).toHaveBeenCalledWith("h3-video"));

    await userEvent.click(screen.getByRole("button", { name: "切换提示词" }));
    await userEvent.click(screen.getByRole("button", { name: "新建提示词" }));
    fireEvent.input(screen.getByLabelText("名称"), { target: { value: "我的草稿" } });
    await act(async () => resolvePrompt(FULL_PROMPT));

    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("我的草稿"));
    expect(screen.getByLabelText("正文（Markdown）")).toHaveValue("");
  });

  it("进页拉取列表与端点配置；无会话恢复时自动选中首条作为本轮基础提示词", async () => {
    renderWorkbench();

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
    renderWorkbench();

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
    renderWorkbench();

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
    renderWorkbench();

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
        expect.anything(), // AbortSignal（N1④ 停止生成）
      );
    });
    expect(await screen.findByText("打标结果 caption")).toBeInTheDocument();
    expect(screen.getByText(/model-a · \d+\.\d+ 秒/)).toBeInTheDocument();
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
    renderWorkbench();

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
    renderWorkbench();

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    await userEvent.type(screen.getByLabelText("打标指令"), "给这张图打个标");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    // done 后：终稿上屏，思考区不再随流式面板一起消失，仍可展开回看。
    expect(await screen.findByText("打标结果")).toBeInTheDocument();
    expect(screen.queryByText("生成中…")).not.toBeInTheDocument();
    expect(screen.getByText("先想想构图")).toBeInTheDocument();
    // 摘要带思考耗时（V7）：用正则匹配前缀。
    expect(screen.getByText(/思考过程/)).toBeInTheDocument();
  });

  it("提示词库为空时发送：labelStream 收到 null（后端给可操作错误）", async () => {
    apiMock.listPrompts.mockResolvedValue([]);
    renderWorkbench();

    await waitFor(() => expect(apiMock.listPrompts).toHaveBeenCalled());
    await userEvent.type(screen.getByLabelText("打标指令"), "打标");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(apiMock.labelStream).toHaveBeenCalledWith(
        expect.objectContaining({ prompt_name: null }),
        expect.anything(),
        expect.anything(), // AbortSignal
      );
    });
  });

  it("端点切换器选择另一套配置：调 activateEndpoint 并更新 chip", async () => {
    apiMock.activateEndpoint.mockResolvedValue(undefined);
    renderWorkbench();

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
    renderWorkbench();

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
    renderWorkbench();

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    fireEvent.change(screen.getByLabelText("附图或视频（最多 1 个）"), {
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
        expect.anything(), // AbortSignal
      );
    });
    vi.unstubAllGlobals();
  });

  it("生成中卸载再重挂工作台（模拟切页往返），流式面板接上、回复照常上屏", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    apiMock.labelStream.mockImplementation(async (_payload, handlers) => {
      handlers.onStart("s1");
      handlers.onDelta("content", "半截");
      await gate;
      handlers.onDone("s1", "切页回归终稿");
    });
    // 与 App 装配同构：会话域在工作台之外，「切页」只是挂载 / 卸载工作台。
    function Toggle({ show }: { show: boolean }) {
      return show ? <PromptWorkbench onNavigateToSettings={() => {}} /> : null;
    }
    const { rerender } = render(
      <ChatSessionProvider>
        <Toggle show />
      </ChatSessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("名称")).toHaveValue("h3-video");
    });
    await userEvent.type(screen.getByLabelText("打标指令"), "打个标");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(screen.getByText("半截")).toBeInTheDocument());

    // 切走（卸载）→ 切回（重挂）：会话域不随组件生命周期重建，流式面板原样接上。
    rerender(
      <ChatSessionProvider>
        <Toggle show={false} />
      </ChatSessionProvider>,
    );
    rerender(
      <ChatSessionProvider>
        <Toggle show />
      </ChatSessionProvider>,
    );
    expect(screen.getByText("生成中…")).toBeInTheDocument();

    release();
    expect(await screen.findByText("切页回归终稿")).toBeInTheDocument();
    expect(screen.queryByText("生成中…")).not.toBeInTheDocument();
  });
});

describe("编辑器镜像与恢复优先级（三期）", () => {
  beforeEach(() => {
    // 镜像键被既有用例的编辑器动作写过：每个用例先清档，防串状态。
    localStorage.clear();
  });

  const MIRROR_BASE = {
    draftDescription: "",
    savedPrompt: { name: "simple", description: "", body: "" },
  };

  it("镜像优先：编辑器恢复为离开时刻的样子，且不经服务端取全文", async () => {
    localStorage.setItem(
      "dsf-workbench-editor",
      JSON.stringify({
        ...MIRROR_BASE,
        selectedName: "simple",
        draftName: "simple",
        draftBody: "镜像正文",
        isNewDraft: false,
      }),
    );
    renderWorkbench();

    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("simple"));
    expect(screen.getByLabelText("描述")).toHaveValue("");
    expect(screen.getByLabelText("正文（Markdown）")).toHaveValue("镜像正文");
    // 镜像 = 编辑器原样，内容以镜像为准，不额外请求全文。
    expect(apiMock.getPrompt).not.toHaveBeenCalled();
  });

  it("镜像与快照分叉：编辑器听镜像，被切走的会话不复活", async () => {
    localStorage.setItem(
      "dsf-workbench-editor",
      JSON.stringify({
        ...MIRROR_BASE,
        selectedName: "simple",
        draftName: "simple",
        draftBody: "镜像正文",
        isNewDraft: false,
      }),
    );
    apiMock.latestSession.mockResolvedValue({
      session_id: "s-old",
      settings: { prompt_name: "h3-video", skill_names: [] },
      messages: [{ role: "user", text: "旧会话消息", attachment: null }],
    });
    renderWorkbench();

    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("simple"));
    expect(screen.getByLabelText("正文（Markdown）")).toHaveValue("镜像正文");
    // 快照的会话属于 h3-video 的时代，用户切走选择时界面已按产品语义清空——
    // 重启不把它复活（保真到离开时刻）。
    expect(screen.queryByText("旧会话消息")).not.toBeInTheDocument();
  });

  it("镜像与快照一致：编辑器恢复镜像草稿，会话照常恢复", async () => {
    localStorage.setItem(
      "dsf-workbench-editor",
      JSON.stringify({
        selectedName: "h3-video",
        draftName: "h3-video",
        draftDescription: "视频打标",
        draftBody: "未保存的草稿正文",
        savedPrompt: {
          name: "h3-video",
          description: "视频打标",
          body: "你是打标助手。",
        },
        isNewDraft: false,
      }),
    );
    apiMock.latestSession.mockResolvedValue({
      session_id: "s-old",
      settings: { prompt_name: "h3-video", skill_names: [] },
      messages: [{ role: "user", text: "历史消息", attachment: null }],
    });
    renderWorkbench();

    await waitFor(() =>
      expect(screen.getByLabelText("正文（Markdown）")).toHaveValue("未保存的草稿正文"),
    );
    expect(await screen.findByText("历史消息")).toBeInTheDocument();
  });

  it("镜像指向已删除的提示词：编辑器回退默认链，会话不恢复", async () => {
    localStorage.setItem(
      "dsf-workbench-editor",
      JSON.stringify({
        ...MIRROR_BASE,
        selectedName: "ghost",
        draftName: "ghost",
        draftBody: "幽灵正文",
        isNewDraft: false,
      }),
    );
    apiMock.latestSession.mockResolvedValue({
      session_id: "s-old",
      settings: { prompt_name: "h3-video", skill_names: [] },
      messages: [{ role: "user", text: "旧会话消息", attachment: null }],
    });
    renderWorkbench();

    // 快照被镜像分叉规则拦下 → 无「恢复的提示词」，回落「自动选中首条」。
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue("h3-video"));
    expect(screen.getByLabelText("正文（Markdown）")).toHaveValue("你是打标助手。");
    expect(screen.queryByText("旧会话消息")).not.toBeInTheDocument();
  });

  it("空白编辑器里直接打的草稿也恢复（无选中但有草稿内容）", async () => {
    localStorage.setItem(
      "dsf-workbench-editor",
      JSON.stringify({
        selectedName: "",
        draftName: "凭空起的名",
        draftDescription: "",
        draftBody: "写了一半的正文",
        savedPrompt: { name: "", description: "", body: "" },
        isNewDraft: false,
      }),
    );
    renderWorkbench();

    await waitFor(() =>
      expect(screen.getByLabelText("名称")).toHaveValue("凭空起的名"),
    );
    expect(screen.getByLabelText("正文（Markdown）")).toHaveValue("写了一半的正文");
  });

  it("新建草稿态的镜像恢复：保持新建态而不是回落首条", async () => {
    localStorage.setItem(
      "dsf-workbench-editor",
      JSON.stringify({
        selectedName: "",
        draftName: "",
        draftDescription: "",
        draftBody: "",
        savedPrompt: { name: "", description: "", body: "" },
        isNewDraft: true,
      }),
    );
    renderWorkbench();

    await waitFor(() =>
      expect(screen.getByLabelText("正文（Markdown）")).toHaveValue(""),
    );
    expect(screen.getByLabelText("名称")).toHaveValue("");
    // 交互让位已发生：后端快照 / 首条都不应覆盖新建态。
    expect(apiMock.getPrompt).not.toHaveBeenCalled();
  });
});
