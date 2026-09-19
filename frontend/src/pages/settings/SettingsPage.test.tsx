import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EndpointConfigSummary, SkillInfo } from "../../api";
import { SettingsPage } from "./SettingsPage";

// 设置页测试只关心「交互 → 调了哪个 API → 界面状态对不对」，api 层整体 mock 掉。
// 注意查询唯一性：选中的名称同时出现在列表行与详情标题里，断言一律用 label / 唯一文本。
const apiMock = vi.hoisted(() => ({
  listEndpoints: vi.fn(),
  createEndpoint: vi.fn(),
  updateEndpoint: vi.fn(),
  deleteEndpoint: vi.fn(),
  activateEndpoint: vi.fn(),
  testEndpoint: vi.fn(),
  listSkills: vi.fn(),
  importSkill: vi.fn(),
  importSkillFiles: vi.fn(),
  importSkillFile: vi.fn(),
  setSkillEnabled: vi.fn(),
  renameSkill: vi.fn(),
  deleteSkill: vi.fn(),
  listSkillFiles: vi.fn(),
  readSkillFile: vi.fn(),
  saveSkillFile: vi.fn(),
  listDirectory: vi.fn(),
}));

// 部分 mock：只替掉 api 对象，ApiError / errorMessage 用真货——错误分档要靠真类的 kind 字段判。
vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: apiMock,
}));

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
  { name: "h3-skill", description: "H3 官方要求", enabled: true, body_chars: 1240 },
  { name: "old-skill", description: "", enabled: false, body_chars: 320 },
];

const SKILL_FILES = {
  name: "h3-skill",
  files: [
    { path: "SKILL.md", role: "skill", previewable: true },
    { path: "references/detail.md", role: "reference", previewable: true },
    { path: "assets/cover.png", role: "asset", previewable: false },
  ],
};

/** SKILL.md 的正文（读写两侧共用一份，断言里不必再抄一遍带转义的字符串）。 */
const SKILL_MD = "# Example\n按格式输出 caption。";

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.listEndpoints.mockResolvedValue(ENDPOINTS);
  apiMock.listSkills.mockResolvedValue(SKILLS);
  apiMock.listSkillFiles.mockResolvedValue(SKILL_FILES);
  apiMock.readSkillFile.mockResolvedValue({ path: "SKILL.md", content: SKILL_MD });
});

describe("SettingsPage · 连接·端点配置", () => {
  it("技能开关等待响应时禁用重复提交，失败后恢复操作", async () => {
    let rejectToggle: (error: Error) => void = () => {};
    apiMock.setSkillEnabled.mockImplementationOnce(
      () =>
        new Promise<void>((_resolve, reject) => {
          rejectToggle = reject;
        }),
    );
    render(<SettingsPage section="skills" />);
    const toggle = await screen.findByLabelText("启用 h3-skill");

    await userEvent.click(toggle);
    await userEvent.click(toggle);
    expect(toggle).toBeDisabled();
    expect(apiMock.setSkillEnabled).toHaveBeenCalledTimes(1);
    rejectToggle(new Error("写入失败"));

    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(await screen.findByText(/写入失败/)).toBeVisible();
  });

  it("列表 + 详情回填：名称只读、密钥只报来源不回内容", async () => {
    render(<SettingsPage />);

    await waitFor(() => {
      expect(screen.getByLabelText("Base URL")).toHaveValue("https://a/v1");
    });
    expect(screen.getByText("backup")).toBeInTheDocument();
    expect(screen.getByLabelText("名称")).toHaveValue("default");
    expect(screen.getByLabelText("名称")).toBeDisabled();
    expect(screen.getByLabelText("模型名称")).toHaveValue("model-a");
    expect(screen.getByText(/已配置 · 来源：credentials 文件/)).toBeInTheDocument();
  });

  it("保存更改：不带密钥调 updateEndpoint（后端沿用已存密钥）", async () => {
    apiMock.updateEndpoint.mockResolvedValue(ENDPOINTS[0]);
    render(<SettingsPage />);

    await waitFor(() => screen.getByLabelText("Base URL"));
    await userEvent.clear(screen.getByLabelText("模型名称"));
    await userEvent.type(screen.getByLabelText("模型名称"), "model-a2");
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));

    await waitFor(() => {
      expect(apiMock.updateEndpoint).toHaveBeenCalledWith("default", {
        base_url: "https://a/v1",
        model: "model-a2",
        api_format: "openai-chat-completions",
        request_params: {},
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
      request_params: {},
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
    await userEvent.type(screen.getByLabelText("模型名称"), "m");
    await userEvent.type(screen.getByLabelText("API 密钥"), "sk-new-key"); // pragma: allowlist secret —— 测试假密钥
    await userEvent.click(screen.getByRole("button", { name: "创建配置" }));

    await waitFor(() => {
      expect(apiMock.createEndpoint).toHaveBeenCalledWith({
        name: "new-one",
        base_url: "https://n/v1",
        model: "m",
        api_format: "openai-chat-completions",
        request_params: {},
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

describe("SettingsPage · 端点配置·测试连接", () => {
  it("测试连接：调 testEndpoint 并显示结果与耗时", async () => {
    apiMock.testEndpoint.mockResolvedValue({
      ok: true,
      message: "连接成功，模型应答正常。",
      latency_ms: 412,
    });
    render(<SettingsPage />);
    await screen.findByText("model-a");

    await userEvent.click(screen.getByRole("button", { name: "测试连接" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        "连接成功，模型应答正常。 · 412 ms",
      );
    });
    expect(apiMock.testEndpoint).toHaveBeenCalledWith({
      base_url: "https://a/v1",
      model: "model-a",
      api_format: "openai-chat-completions",
      name: "default",
    });
  });

  it("测试连接失败：显示后端分类提示", async () => {
    apiMock.testEndpoint.mockResolvedValue({
      ok: false,
      message: "鉴权失败：API 密钥无效或过期。",
      latency_ms: 120,
    });
    render(<SettingsPage />);
    await screen.findByText("model-a");

    await userEvent.click(screen.getByRole("button", { name: "测试连接" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("鉴权失败");
    });
  });
});

describe("SettingsPage · 端点配置·高级参数", () => {
  const TUNED: EndpointConfigSummary = {
    name: "tuned",
    base_url: "https://t/v1",
    model: "model-t",
    api_format: "openai-chat-completions",
    has_api_key: true,
    is_active: true,
    request_params: {
      temperature: 0.7,
      extra_body: { top_k: 50 },
    },
  };

  async function openAdvanced(): Promise<void> {
    apiMock.listEndpoints.mockResolvedValue([TUNED]);
    render(<SettingsPage />);
    await waitFor(() => screen.getByLabelText("Base URL"));
    await userEvent.click(screen.getByRole("button", { name: /高级参数（可选）/ }));
  }

  it("默认折叠；展开后模型通用参数（表单 + JSON）与传输参数两组齐备", async () => {
    render(<SettingsPage />);
    await waitFor(() => screen.getByLabelText("Base URL"));
    expect(screen.queryByLabelText("temperature")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /高级参数（可选）/ }));

    expect(screen.getByLabelText("temperature")).toBeInTheDocument();
    expect(screen.getByLabelText("top_p")).toBeInTheDocument();
    expect(screen.getByLabelText("max_tokens")).toBeInTheDocument();
    expect(screen.getByLabelText("模型通用参数 JSON")).toBeInTheDocument();
    expect(screen.getByLabelText(/timeout_seconds/)).toBeInTheDocument();
    expect(screen.getByLabelText(/max_retries/)).toBeInTheDocument();
  });

  it("落盘参数回填：表单与 JSON 一致显示（extra_body 在 JSON 里）", async () => {
    await openAdvanced();

    expect(screen.getByLabelText("temperature")).toHaveValue(0.7);
    expect(screen.getByLabelText("top_p")).toHaveValue(null);
    expect(screen.getByLabelText("模型通用参数 JSON")).toHaveValue(
      JSON.stringify({ temperature: 0.7, extra_body: { top_k: 50 } }, null, 2),
    );
  });

  it("粘贴厂商风格 JSON：已知键同步进表单，未知键提示已忽略（不报错）", async () => {
    await openAdvanced();
    const json = screen.getByLabelText("模型通用参数 JSON");

    // JSON 含 {} 字符（userEvent.type 会误当按键语法）；组件走 onInput，用 fireEvent.input。
    fireEvent.input(json, { target: { value: '{"temperature":0.2,"top_k":40}' } });

    expect(screen.getByLabelText("temperature")).toHaveValue(0.2);
    expect(screen.getByRole("status")).toHaveTextContent("已忽略：top_k");
  });

  it("JSON 无效时保存被拦下（不发请求），提示修好再保存", async () => {
    await openAdvanced();
    const json = screen.getByLabelText("模型通用参数 JSON");

    fireEvent.input(json, { target: { value: "{oops" } });
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));

    expect(apiMock.updateEndpoint).not.toHaveBeenCalled();
    expect(await screen.findByText(/JSON 写法无效/)).toBeInTheDocument();
  });

  it("旧后端响应缺 request_params 字段时详情页不崩（热升级窗口防御）", async () => {
    // 模拟「旧后端进程 + 新页面」的热升级窗口：响应没有 request_params 键
    // （新契约里必填；实机白屏事故 2026-09-13 的根因）。缺字段按未设置处理。
    const legacy = { ...TUNED } as Record<string, unknown>;
    delete legacy.request_params;
    apiMock.listEndpoints.mockResolvedValue([
      legacy as unknown as EndpointConfigSummary,
    ]);
    render(<SettingsPage />);
    await waitFor(() => screen.getByLabelText("Base URL"));

    expect(screen.getByLabelText("Base URL")).toHaveValue("https://t/v1");
    await userEvent.click(screen.getByRole("button", { name: /高级参数（可选）/ }));
    expect(screen.getByLabelText("temperature")).toHaveValue(null);
    // 无参数 = 空串展示（占位符给 JSON 示例，2026-09-13 用户反馈）。
    expect(screen.getByLabelText("模型通用参数 JSON")).toHaveValue("");
    expect(screen.getByLabelText("模型通用参数 JSON")).toHaveAttribute(
      "placeholder",
      expect.stringContaining('"extra_body"'),
    );
  });

  it("保存携带高级参数：表单值 + JSON 里的 extra_body 一起进载荷", async () => {
    apiMock.updateEndpoint.mockResolvedValue(TUNED);
    await openAdvanced();

    await userEvent.clear(screen.getByLabelText("temperature"));
    await userEvent.type(screen.getByLabelText("temperature"), "0.8");
    await userEvent.type(screen.getByLabelText(/timeout_seconds/), "240");
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));

    await waitFor(() => {
      expect(apiMock.updateEndpoint).toHaveBeenCalledWith("tuned", {
        base_url: "https://t/v1",
        model: "model-t",
        api_format: "openai-chat-completions",
        request_params: {
          temperature: 0.8,
          extra_body: { top_k: 50 },
          timeout_seconds: 240,
        },
      });
    });
  });
});

describe("SettingsPage · 能力·技能", () => {
  async function openSkills(): Promise<void> {
    render(<SettingsPage section="skills" />);
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

  it("列表卡片：名称旁显示注入字数徽标（注入正文字符数）", async () => {
    await openSkills();

    expect(screen.getByText("1.2k 字")).toBeInTheDocument();
    expect(screen.getByText("320 字")).toBeInTheDocument();
    // 注入量提示走自研气泡（Tip），不再用原生 title；徽标本体仍在名称旁。
    expect(screen.queryByTitle(/注入正文字符数/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/字$/).length).toBe(2);
  });

  it("搜索框按名称过滤列表", async () => {
    await openSkills();

    await userEvent.type(screen.getByLabelText("搜索技能"), "old");

    expect(screen.getByLabelText("启用 old-skill")).toBeInTheDocument();
    expect(screen.queryByLabelText("启用 h3-skill")).not.toBeInTheDocument();
  });

  it("损坏包降级呈现：description 以「文件损坏：」开头时原文照排、不吞不替换（宽容降级口径）", async () => {
    apiMock.listSkills.mockResolvedValue([
      {
        name: "bad",
        description:
          "文件损坏：SKILL.md 的 frontmatter 未闭合（开头有 ---，但找不到结束的 ---）。",
        enabled: true,
        body_chars: 0,
      },
    ] satisfies SkillInfo[]);
    render(<SettingsPage section="skills" />);
    await screen.findByLabelText("启用 bad");

    // TooltipContent 会另渲染一份描述文本（portal 到 body），断言收窄到列表行内的 span
    const row = screen.getByLabelText("启用 bad").parentElement;
    const text = await within(row as HTMLElement).findByText(/文件损坏：/);

    // jsdom 量不到颜色，颜色一类的视觉口径归 e2e 视觉基线那层；这里只断「损坏信息原文照排、
    // 没有被降级逻辑吞掉或替换」。
    expect(text.textContent).toContain("frontmatter 未闭合");
  });

  it("导入成功：选择文件夹调 importSkillFiles 并给出体积与 token 提醒反馈条", async () => {
    apiMock.importSkillFiles.mockResolvedValue({
      name: "fresh",
      description: "",
      enabled: true,
      total_bytes: 42_000,
    });
    await openSkills();

    await userEvent.click(screen.getByRole("button", { name: "导入 Skill" }));
    const input = screen.getByLabelText("选择 skill 文件夹");
    const folderFile = new File(["# U"], "SKILL.md", { type: "text/markdown" });
    Object.defineProperty(folderFile, "webkitRelativePath", {
      value: "fresh/SKILL.md",
    });
    fireEvent.change(input as HTMLInputElement, { target: { files: [folderFile] } });

    await waitFor(() => {
      expect(apiMock.importSkillFiles).toHaveBeenCalledTimes(1);
    });
    expect(
      await screen.findByText(/41\.0 KiB——skill 全文将注入打标请求/),
    ).toBeInTheDocument();
  });

  it("服务器选择器确认后回填技能文件，点击导入才提交", async () => {
    apiMock.listDirectory.mockResolvedValue({
      hostname: "skill-server",
      system: "Linux",
      path: "/srv/skills",
      parent: "/srv",
      unavailable_count: 0,
      entries: [
        {
          name: "notes.txt",
          path: "/srv/skills/notes.txt",
          kind: "file",
          size: 24,
          modified_at: "2026-09-17T00:00:00Z",
        },
      ],
    });
    apiMock.importSkill.mockResolvedValue({ name: "notes", total_bytes: 24 });
    await openSkills();

    await userEvent.click(screen.getByRole("button", { name: "导入 Skill" }));
    await userEvent.click(screen.getByRole("button", { name: "选择技能目录或文件" }));
    const picker = within(
      await screen.findByRole("dialog", { name: "选择目录或文件" }),
    );
    await userEvent.click(await picker.findByRole("button", { name: "notes.txt" }));
    await userEvent.click(picker.getByRole("button", { name: "选择此文件" }));

    expect(apiMock.listDirectory).toHaveBeenCalledWith("", true, false, [
      ".md",
      ".txt",
    ]);
    expect(screen.getByLabelText("skill 服务器路径")).toHaveValue(
      "/srv/skills/notes.txt",
    );
    expect(apiMock.importSkill).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "导入" }));
    expect(apiMock.importSkill).toHaveBeenCalledWith("/srv/skills/notes.txt");
  });

  it("路径导入：粘贴路径点导入调 importSkill；空路径时导入按钮禁用", async () => {
    apiMock.importSkill.mockResolvedValue({
      name: "path-skill",
      description: "",
      enabled: true,
      total_bytes: 1024,
    });
    await openSkills();

    await userEvent.click(screen.getByRole("button", { name: "导入 Skill" }));
    const importButton = screen.getByRole("button", { name: "导入" });
    expect(importButton).toBeDisabled();

    await userEvent.type(screen.getByLabelText("skill 服务器路径"), "C:/skills/demo");
    expect(importButton).toBeEnabled();
    await userEvent.click(importButton);

    await waitFor(() => {
      expect(apiMock.importSkill).toHaveBeenCalledWith("C:/skills/demo");
    });
    expect(await screen.findByText(/已导入「path-skill」/)).toBeInTheDocument();
  });

  it("单文件导入：SKILL.md 文件入口调 importSkillFile（单文件 skill 无文件夹结构）", async () => {
    apiMock.importSkillFile.mockResolvedValue({
      name: "single",
      description: "",
      enabled: true,
      total_bytes: 2048,
    });
    await openSkills();

    await userEvent.click(screen.getByRole("button", { name: "导入 Skill" }));
    fireEvent.change(screen.getByLabelText("选择 SKILL.md 文件"), {
      target: {
        files: [new File(["# S"], "my-skill.md", { type: "text/markdown" })],
      },
    });

    await waitFor(() => {
      expect(apiMock.importSkillFile).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText(/已导入「single」/)).toBeInTheDocument();
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

  it("技能正文与描述保存携带原始基线，失败后保留草稿并可重试", async () => {
    apiMock.saveSkillFile.mockRejectedValueOnce(new Error("文件已被修改"));
    apiMock.saveSkillFile.mockResolvedValueOnce({
      path: "SKILL.md",
      content: "保存后的正文",
    });
    await openSkills();
    const editor = await screen.findByLabelText("技能文件内容");
    await waitFor(() => expect(editor).toHaveValue("# Example\n按格式输出 caption。"));

    fireEvent.change(editor, { target: { value: "新的正文" } });
    fireEvent.change(screen.getByLabelText("技能描述"), {
      target: { value: "新的描述" },
    });
    expect(screen.getByRole("button", { name: "references/detail.md" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));

    expect(await screen.findByText(/文件已被修改/)).toBeInTheDocument();
    expect(editor).toHaveValue("新的正文");
    expect(apiMock.saveSkillFile).toHaveBeenCalledWith("h3-skill", "SKILL.md", {
      content: "新的正文",
      original_content: "# Example\n按格式输出 caption。",
      description: "新的描述",
    });
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));
    await waitFor(() => expect(editor).toHaveValue("保存后的正文"));
    expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled();
  });

  it("改名称：先按原名存正文、再改名，选中项与列表跟到新名", async () => {
    const renamed: SkillInfo[] = SKILLS.map((item) =>
      item.name === "h3-skill" ? { ...item, name: "h3-caption" } : item,
    );
    apiMock.listSkills.mockResolvedValueOnce(SKILLS).mockResolvedValue(renamed);
    apiMock.renameSkill.mockResolvedValue(undefined);
    apiMock.saveSkillFile.mockResolvedValue({ path: "SKILL.md", content: "新的正文" });
    await openSkills();

    const nameField = await screen.findByLabelText("技能名称");
    expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled();
    fireEvent.change(nameField, { target: { value: "h3-caption" } });
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));

    // 改名必须排在存内容之后：改名会重写 frontmatter，先改名就让刚读的基线失效（PUT 400）。
    expect(apiMock.saveSkillFile).toHaveBeenCalledWith("h3-skill", "SKILL.md", {
      content: SKILL_MD,
      original_content: SKILL_MD,
    });
    await waitFor(() =>
      expect(apiMock.renameSkill).toHaveBeenCalledWith("h3-skill", {
        new_name: "h3-caption",
      }),
    );
    expect(screen.getByLabelText("技能名称")).toHaveValue("h3-caption");
    expect(screen.getByText("已保存并改名为「h3-caption」")).toBeInTheDocument();
    expect(screen.getByLabelText("启用 h3-caption")).toBeInTheDocument();
  });

  it("改名撞上已有技能：保留输入与草稿，就地给出后端的原因", async () => {
    apiMock.renameSkill.mockRejectedValue(new Error("已有同名 skill：old-skill"));
    await openSkills();

    fireEvent.change(await screen.findByLabelText("技能名称"), {
      target: { value: "old-skill" },
    });
    await userEvent.click(screen.getByRole("button", { name: "保存更改" }));

    expect(await screen.findByText(/已有同名 skill/)).toBeInTheDocument();
    expect(screen.getByLabelText("技能名称")).toHaveValue("old-skill");
    expect(screen.getByLabelText("启用 h3-skill")).toBeInTheDocument();
  });

  it("放弃技能草稿恢复内容并解锁文件切换", async () => {
    await openSkills();
    const editor = await screen.findByLabelText("技能文件内容");
    await waitFor(() => expect(editor).toHaveValue("# Example\n按格式输出 caption。"));

    fireEvent.change(editor, { target: { value: "未保存" } });
    await userEvent.click(screen.getByRole("button", { name: "放弃更改" }));

    expect(editor).toHaveValue("# Example\n按格式输出 caption。");
    expect(screen.getByRole("button", { name: "references/detail.md" })).toBeEnabled();
  });

  it("拖入单文件通过导入接口提交一次并显示结果", async () => {
    apiMock.importSkillFile.mockResolvedValue({ name: "dropped", total_bytes: 12 });
    await openSkills();
    await userEvent.click(screen.getByRole("button", { name: "导入 Skill" }));
    const file = new File(["skill"], "SKILL.md");

    fireEvent.drop(screen.getByRole("button", { name: "拖入 Skill 包" }), {
      dataTransfer: { items: [], files: [file] },
    });

    await waitFor(() =>
      expect(apiMock.importSkillFile).toHaveBeenCalledExactlyOnceWith(file),
    );
    expect(await screen.findByText(/已导入「dropped」/)).toBeInTheDocument();
  });
});
