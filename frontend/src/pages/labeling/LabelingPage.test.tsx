import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import type { components } from "../../api-types.gen";
import { LabelingPage } from "./LabelingPage";

vi.mock("../../api", () => ({
  api: {
    listWorkdirs: vi.fn(),
    listBatches: vi.fn(),
    listItems: vi.fn(),
    readCaption: vi.fn().mockResolvedValue("caption"),
    testEndpoint: vi
      .fn()
      .mockResolvedValue({ ok: true, message: "连通", latency_ms: 1 }),
    addRetryItems: vi.fn(),
    removeRetryItem: vi.fn(),
    clearRetryItems: vi.fn(),
    importMaterials: vi.fn(),
    removeUnimported: vi.fn(),
    getTask: vi.fn(),
    cancelTask: vi.fn(),
    getBatchSnapshot: vi.fn(),
    currentRun: vi.fn().mockResolvedValue({ status: "completed" }),
    latestRun: vi.fn(),
    exportPlan: vi.fn().mockResolvedValue({
      batch: 1,
      included: [],
      excluded: [],
      total_bytes: 0,
      sequential: true,
      non_ascii_names: false,
    }),
  },
  ApiError: class extends Error {},
  errorMessage: (error: unknown) => String(error),
}));

function view(name: string): components["schemas"]["ItemListView"] {
  return {
    batch: 1,
    query: "",
    groups: {
      done: [
        {
          item: name,
          name: `${name}.jpg`,
          status: "done",
          media: "image",
          in_retry: false,
          can_retry: true,
        },
      ],
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // 切页记忆走 localStorage：清档防止用例间串状态。
  localStorage.clear();
  vi.mocked(api.currentRun).mockResolvedValue({
    run_id: "old",
    status: "completed",
    batch: 1,
    mode: "full",
    counters: {},
    current_item: null,
    error: null,
  });
  vi.mocked(api.listWorkdirs).mockResolvedValue([
    { id: "one", title: "目录一", path: "/one", last_used_at: 0 },
    { id: "two", title: "目录二", path: "/two", last_used_at: 0 },
  ]);
  vi.mocked(api.listBatches).mockResolvedValue([
    {
      id: "s1",
      seq: 1,
      name: "策略",
      active: true,
      created_at: "",
      description: "",
      product_count: 1,
    },
  ]);
  vi.mocked(api.listItems).mockResolvedValue(view("first"));
  vi.mocked(api.latestRun).mockResolvedValue({
    record: null,
    log_path: null,
    items_path: null,
  });
  vi.mocked(api.getBatchSnapshot).mockResolvedValue({
    built_at: "2026-09-18T00:00:00Z",
    changed: false,
    endpoint: {
      api_format: "openai-chat",
      base_url: "https://example.test/v1",
      model: "mock-model",
      name: "Mock endpoint",
      request_params: {},
      sha256: "endpoint",
    },
    prompt: { body: "prompt", name: "Mock prompt", sha256: "prompt" },
    recorded_sha256: "snapshot",
    sha256: "snapshot",
    skills: [{ body: "skill", name: "Mock skill", sha256: "skill" }],
    tool_version: "0.1.0",
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("打标页读取流程", () => {
  it("一处目录读取失败不会阻止其他目录使用，错误目录仍可定位", async () => {
    vi.mocked(api.listBatches).mockImplementation(async (wid) => {
      if (wid === "one") throw new Error("目录不存在");
      return [
        {
          id: "s1",
          seq: 1,
          name: "策略",
          active: true,
          created_at: "",
          description: "",
          product_count: 1,
        },
      ];
    });
    const user = userEvent.setup();
    await act(async () => {
      render(<LabelingPage />);
    });

    expect(
      await screen.findByRole("button", { name: "first.jpg" }),
    ).toBeInTheDocument();
    expect(api.listItems).toHaveBeenCalledWith("two", "s1");
    await user.click(screen.getByRole("button", { name: "选择工作目录与批次" }));
    const unavailable = within(screen.getByRole("group", { name: "目录一" }));
    expect(unavailable.getByText(/目录不存在/)).toBeInTheDocument();
    expect(
      unavailable.getByRole("button", { name: "工作目录设置 目录一" }),
    ).toBeEnabled();
    expect(unavailable.getByRole("button", { name: "新增策略 目录一" })).toBeDisabled();
  });

  it("提示词与技能同名时仍分别展示且没有重复 key", async () => {
    const snapshot = await api.getBatchSnapshot("one", "s1");
    snapshot.prompt.name = "shared";
    snapshot.skills = [{ name: "shared", body: "skill", sha256: "skill" }];
    vi.mocked(api.getBatchSnapshot).mockResolvedValue(snapshot);
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      render(<LabelingPage />);
      const context = await screen.findByRole("region", { name: "策略配置" });

      expect(within(context).getAllByText("shared")).toHaveLength(2);
      expect(errors).not.toHaveBeenCalled();
    } finally {
      errors.mockRestore();
    }
  });

  it("顶栏展示批次快照的只读端点、提示词与技能上下文", async () => {
    render(<LabelingPage />);
    const context = await screen.findByRole("region", { name: "策略配置" });
    expect(context).toHaveTextContent("Example · mock-model");
    expect(context).toHaveTextContent("Mock prompt");
    expect(context).toHaveTextContent("Mock skill");
  });

  it("自动预览当前运行素材，手动查看与返回概览不会被运行刷新抢走", async () => {
    let source: EventTarget | undefined;
    vi.stubGlobal(
      "EventSource",
      class extends EventTarget {
        constructor() {
          super();
          source = this;
        }
        close(): void {}
      },
    );
    vi.mocked(api.currentRun).mockResolvedValue({
      run_id: "run",
      status: "running",
      batch: 1,
      mode: "full",
      counters: { planned: 2, attempted: 0 },
      current_item: "first",
      error: null,
    });
    const initial = view("first");
    initial.groups.done = [
      ...(initial.groups.done ?? []),
      ...(view("second").groups.done ?? []),
    ];
    vi.mocked(api.listItems).mockResolvedValue(initial);
    const user = userEvent.setup();
    render(<LabelingPage />);

    expect(
      await screen.findByRole("heading", { name: "first.jpg" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "second.jpg" }));
    await act(async () => source?.dispatchEvent(new Event("run-started")));
    expect(screen.getByRole("heading", { name: "second.jpg" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回概览" }));
    await act(async () => source?.dispatchEvent(new Event("run-started")));
    expect(
      screen.queryByRole("heading", { name: "first.jpg" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "second.jpg" }),
    ).not.toBeInTheDocument();
  });

  it("未导入删除先确认，成功刷新清单且保留同主干在册素材", async () => {
    const initial = view("first");
    initial.groups.unimported = [
      {
        item: "first",
        name: "first.psd",
        status: "unimported",
        media: "file",
        can_retry: false,
        in_retry: false,
        reason: "扩展名不支持",
      },
    ];
    vi.mocked(api.listItems).mockResolvedValue(initial);
    vi.mocked(api.removeUnimported).mockResolvedValue({
      count: 1,
      recovery_path: "/one/.dsf/trash/selected",
    });
    const user = userEvent.setup();
    render(<LabelingPage />);
    await user.click(
      await screen.findByRole(
        "button",
        { name: "删除未导入 first.psd" },
        { timeout: 5_000 },
      ),
    );
    expect(api.removeUnimported).not.toHaveBeenCalled();
    const dialog = within(screen.getByRole("dialog"));
    vi.mocked(api.listItems).mockResolvedValue(view("first"));

    await user.click(dialog.getByRole("button", { name: "删除" }));

    expect(api.removeUnimported).toHaveBeenCalledExactlyOnceWith("one", ["first.psd"]);
    expect(await dialog.findByRole("status")).toHaveTextContent(
      "/one/.dsf/trash/selected",
    );
    const closeButton = dialog.getAllByRole("button", { name: "关闭" }).at(0);
    if (!closeButton) throw new Error("删除结果缺少关闭按钮");
    await user.click(closeButton);
    expect(
      await screen.findByRole("button", { name: "first.jpg" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "删除未导入 first.psd" }),
    ).not.toBeInTheDocument();
  });

  it("同主干未导入文件独立预览，批量补登记只提交合资格文件", async () => {
    const initial = view("first");
    initial.groups.unimported = [
      {
        item: "first",
        name: "first.psd",
        status: "unimported",
        media: "file",
        can_retry: false,
        in_retry: false,
        reason: "扩展名不支持",
      },
      {
        item: "new",
        name: "new.jpg",
        status: "unimported",
        media: "image",
        can_retry: false,
        in_retry: false,
        reason: "未登记",
      },
    ];
    vi.mocked(api.listItems).mockResolvedValue(initial);
    vi.mocked(api.importMaterials).mockResolvedValue({ task_id: "selected-import" });
    vi.mocked(api.getTask).mockResolvedValue({
      id: "selected-import",
      status: "succeeded",
      progress: 1,
      error: null,
      result: {
        source: "/one",
        imported: ["new.jpg"],
        skipped_identical: [],
        skipped_conflict: [],
        skipped_duplicate: [],
        rejected: [],
      },
    });
    const user = userEvent.setup();
    render(<LabelingPage />);
    await screen.findByRole("button", { name: "first.jpg" });

    await user.click(screen.getByRole("button", { name: /^first\.psd/ }));

    expect(screen.getByRole("heading", { name: "first.psd" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "产物预览" })).not.toBeInTheDocument();
    const unimported = within(screen.getByRole("region", { name: "未导入" }));
    expect(unimported.getAllByRole("button", { name: "导入" })[0]).toBeDisabled();
    await user.click(unimported.getByRole("button", { name: "一键导入" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.queryByText("first.psd")).not.toBeInTheDocument();
    await user.click(dialog.getByRole("button", { name: "导入" }));

    await dialog.findByText(/导入完成/);
    expect(api.importMaterials).toHaveBeenCalledExactlyOnceWith("one", {
      source: null,
      names: ["new.jpg"],
    });
  });
  it("折叠和展开保留同一视频元素与播放位置，返回关闭预览", async () => {
    const user = userEvent.setup();
    const initial = view("clip");
    const material = initial.groups.done?.[0];
    if (!material) throw new Error("缺少素材");
    material.name = "clip.mp4";
    material.media = "video";
    vi.mocked(api.listItems).mockResolvedValue(initial);
    render(<LabelingPage />);
    await user.click(await screen.findByRole("button", { name: "clip.mp4" }));
    const video = screen.getByLabelText("clip.mp4") as HTMLVideoElement;
    video.currentTime = 12;

    await user.click(screen.getByRole("button", { name: "折叠为小图" }));

    expect(screen.getByLabelText("clip.mp4")).toBe(video);
    expect(video.currentTime).toBe(12);
    // 折叠态由切换器的文案体现（几何尺寸 jsdom 量不到，视觉口径归 e2e 视觉基线那层）。
    expect(screen.getByRole("button", { name: "展开素材" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "展开素材" }));
    expect(screen.getByLabelText("clip.mp4")).toBe(video);
    expect(video.currentTime).toBe(12);
    expect(screen.getByRole("button", { name: "折叠为小图" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "返回概览" }));
    expect(screen.queryByLabelText("clip.mp4")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "产物预览" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "clip.mp4" })).toBeInTheDocument();
  });
  it("发车确认可进入真实导入，完成后重新读取当前批次", async () => {
    const user = userEvent.setup();
    const initial = view("first");
    initial.groups.unimported = [
      {
        item: "new",
        name: "new.jpg",
        media: "image",
        status: "unimported",
        can_retry: false,
        in_retry: false,
        reason: "未登记",
      },
    ];
    vi.mocked(api.listItems).mockResolvedValue(initial);
    vi.mocked(api.importMaterials).mockResolvedValue({ task_id: "import-task" });
    vi.mocked(api.getTask).mockResolvedValue({
      id: "import-task",
      status: "succeeded",
      progress: 1,
      error: null,
      result: {
        source: "/one",
        imported: ["new.jpg"],
        skipped_identical: [],
        skipped_conflict: [],
        skipped_duplicate: [],
        rejected: [],
      },
    });
    render(<LabelingPage />);
    await screen.findByRole("button", { name: "first.jpg" });
    await user.click(screen.getByRole("button", { name: "开始打标" }));

    await user.click(screen.getByRole("button", { name: "先去导入" }));
    expect(screen.getByRole("dialog", { name: "导入素材" })).toBeInTheDocument();
    const reads = vi.mocked(api.listItems).mock.calls.length;
    await user.click(screen.getByRole("button", { name: "导入" }));

    await screen.findByText(/导入完成 · 新增 1 项/);
    expect(api.importMaterials).toHaveBeenCalledWith("one", { source: null });
    expect(vi.mocked(api.listItems).mock.calls.length).toBeGreaterThan(reads);
    expect(api.listItems).toHaveBeenLastCalledWith("one", "s1");
  });

  it("分组全选后批量加入重试，成功后清空选择并同步重试组", async () => {
    const user = userEvent.setup();
    vi.mocked(api.addRetryItems).mockResolvedValue({
      id: "s1",
      seq: 1,
      items: ["first"],
    });
    render(<LabelingPage />);
    await screen.findByRole("button", { name: "first.jpg" });

    await user.click(
      within(screen.getByRole("complementary", { name: "素材条目" })).getByRole(
        "button",
        { name: "选择" },
      ),
    );
    await user.click(screen.getByRole("button", { name: "已完成全选" }));
    expect(screen.getByRole("checkbox", { name: "选择 first.jpg" })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "加入重试" }));

    expect(api.addRetryItems).toHaveBeenCalledWith("one", "s1", ["first"]);
    expect(screen.getByText("已选 0")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: /first.jpg/, pressed: false }),
    ).toHaveLength(2);
    expect(
      screen
        .getAllByRole("checkbox", { name: "选择 first.jpg" })
        .every((checkbox) => !(checkbox as HTMLInputElement).checked),
    ).toBe(true);
  });

  it("重试行逐条移出后原分组素材保留，整体清空同样以服务器结果更新", async () => {
    const user = userEvent.setup();
    const initial = view("first");
    const row = initial.groups.done?.[0];
    if (!row) throw new Error("缺少测试素材");
    row.in_retry = true;
    initial.groups.retry = [row];
    vi.mocked(api.listItems).mockResolvedValue(initial);
    vi.mocked(api.removeRetryItem).mockResolvedValue({ id: "s1", seq: 1, items: [] });
    vi.mocked(api.clearRetryItems).mockResolvedValue({ id: "s1", seq: 1, items: [] });
    render(<LabelingPage />);

    await user.click(await screen.findByRole("button", { name: "移出重试 first.jpg" }));

    expect(api.removeRetryItem).toHaveBeenCalledWith("one", "s1", "first");
    expect(screen.getByRole("button", { name: "first.jpg" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "移出重试 first.jpg" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "刷新条目" }));
    await user.click(await screen.findByRole("button", { name: "清空名单" }));
    expect(api.clearRetryItems).toHaveBeenCalledWith("one", "s1");
    expect(screen.queryByRole("button", { name: "清空名单" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "first.jpg" })).toBeInTheDocument();
  });

  it("清空请求失败时保留重试名单并呈现原因", async () => {
    const user = userEvent.setup();
    const initial = view("first");
    const row = initial.groups.done?.[0];
    if (!row) throw new Error("缺少测试素材");
    row.in_retry = true;
    initial.groups.retry = [row];
    vi.mocked(api.listItems).mockResolvedValue(initial);
    vi.mocked(api.clearRetryItems).mockRejectedValue(new Error("暂时不可写"));
    render(<LabelingPage />);

    await user.click(await screen.findByRole("button", { name: "清空名单" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("暂时不可写");
    expect(
      screen.getByRole("button", { name: "移出重试 first.jpg" }),
    ).toBeInTheDocument();
  });

  it("加载真实批次清单、搜索并预览素材", async () => {
    const user = userEvent.setup();
    render(<LabelingPage />);

    await user.click(await screen.findByRole("button", { name: "first.jpg" }));

    expect(screen.getByRole("img", { name: "first.jpg" })).toHaveAttribute(
      "src",
      "/api/workdirs/one/items/first/asset",
    );
    expect(api.listItems).toHaveBeenCalledWith("one", "s1");
    await user.type(screen.getByRole("textbox", { name: "搜索条目" }), "absent");
    expect(screen.queryByRole("button", { name: "first.jpg" })).not.toBeInTheDocument();
  });

  it("切换目录后旧请求迟到不能覆盖当前条目", async () => {
    const user = userEvent.setup();
    let resolveOld:
      | ((value: components["schemas"]["ItemListView"]) => void)
      | undefined;
    vi.mocked(api.listItems).mockImplementation((wid) =>
      wid === "one"
        ? new Promise((resolve) => {
            resolveOld = resolve;
          })
        : Promise.resolve(view("second")),
    );
    render(<LabelingPage />);

    await screen.findByText("目录一");
    await user.click(screen.getByRole("button", { name: "选择工作目录与批次" }));
    await user.click(
      within(screen.getByRole("group", { name: "目录二" })).getByRole("menuitem"),
    );
    await screen.findByRole("button", { name: "second.jpg" });
    await act(async () => {
      resolveOld?.(view("stale"));
    });

    expect(screen.queryByText("stale.jpg")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "second.jpg" })).toBeInTheDocument();
  });

  it("目录加载失败显示可读错误而非假空清单", async () => {
    vi.mocked(api.listWorkdirs).mockRejectedValue(new Error("服务不可用"));
    render(<LabelingPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("服务不可用");
  });
});

describe("切页记忆与状态章", () => {
  function twoBatches(): void {
    vi.mocked(api.listBatches).mockResolvedValue([
      {
        id: "s1",
        seq: 1,
        name: "策略",
        active: true,
        created_at: "",
        description: "",
        product_count: 1,
      },
      {
        id: "s2",
        seq: 2,
        name: "策略二",
        active: true,
        created_at: "",
        description: "",
        product_count: 1,
      },
    ]);
  }

  it("切页重挂载后恢复上次的批次选择而非回退默认", async () => {
    twoBatches();
    const user = userEvent.setup();
    const first = render(<LabelingPage />);
    await first.findByRole("button", { name: "first.jpg" });

    await user.click(screen.getByRole("button", { name: "选择工作目录与批次" }));
    // twoBatches 对两个目录都生效，菜单里有两个 /s2/——限定在目录一组内点。
    await user.click(
      within(screen.getByRole("group", { name: "目录一" })).getByRole("menuitem", {
        name: /s2/,
      }),
    );
    await screen.findByRole("button", { name: "first.jpg" });
    first.unmount();

    render(<LabelingPage />);
    await screen.findByRole("button", { name: "first.jpg" });
    expect(api.listItems).toHaveBeenLastCalledWith("one", "s2");
    await user.click(screen.getByRole("button", { name: "选择工作目录与批次" }));
    expect(
      within(screen.getByRole("group", { name: "目录一" }))
        .getByRole("menuitem", {
          name: /s2/,
        })
        .querySelector('[data-testid="batch-is-current"]'),
    ).toBeInTheDocument();
  });

  it("localStorage 里的失效批次回退到默认选择", async () => {
    twoBatches();
    localStorage.setItem(
      "dsf-labeling-selection",
      JSON.stringify({ workdirId: "ghost", batchId: "g1" }),
    );
    render(<LabelingPage />);

    await screen.findByRole("button", { name: "first.jpg" });
    expect(api.listItems).toHaveBeenLastCalledWith("one", "s1");
  });

  it("空闲上报回读磁盘终态而不是抹掉状态章", async () => {
    vi.mocked(api.currentRun).mockResolvedValue(null);
    vi.mocked(api.latestRun).mockResolvedValue({
      record: {
        run_id: "r1",
        batch: 1,
        mode: "full",
        status: "completed",
        started_at: "2026-09-22T00:00:00Z",
        finished_at: "2026-09-22T00:01:00Z",
        counters: {
          attempted: 0,
          failed: 0,
          planned: 0,
          skipped: 0,
          succeeded: 0,
        },
        dsf_version: "0.1.0",
        snapshot: "",
        strategy_hash: "",
        trigger: "manual",
      },
      log_path: null,
      items_path: null,
    });
    render(<LabelingPage />);

    // 挂载后先由 latestRun 读到终态，随后 RunControl 探测空闲报 idle——
    // idle 必须触发重读而不是把刚显示的「已完成」抹掉。（重读是幂等轻量磁盘读，
    // 测试环境下 idle 上报可能与 selection 校验重设交织出多轮，只断言下界。
    // 「已完成」与左列组头同名，徽标断言限定在胶囊内。）
    const capsule = (): HTMLElement =>
      screen.getByRole("button", { name: "选择工作目录与批次" });
    await waitFor(() =>
      expect(within(capsule()).getByText("已完成")).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(vi.mocked(api.latestRun).mock.calls.length).toBeGreaterThanOrEqual(2),
    );
    await waitFor(() =>
      expect(within(capsule()).getByText("已完成")).toBeInTheDocument(),
    );
  });

  it("左列分组折叠状态重挂载后保留", async () => {
    const user = userEvent.setup();
    const first = render(<LabelingPage />);
    await first.findByRole("button", { name: "first.jpg" });
    const header = screen.getByRole("button", { name: /已完成/ });
    expect(header).toHaveAttribute("aria-expanded", "true");
    await user.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    first.unmount();

    render(<LabelingPage />);
    const restored = await screen.findByRole("button", { name: /已完成/ });
    expect(restored).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "first.jpg" })).not.toBeInTheDocument();
  });

  it("组内展开状态重挂载后保留", async () => {
    const initial = view("first");
    initial.groups.done = Array.from({ length: 5 }, (_, index) => ({
      item: `n${index}`,
      name: `n${index}.jpg`,
      status: "done" as const,
      media: "image" as const,
      in_retry: false,
      can_retry: true,
    }));
    vi.mocked(api.listItems).mockResolvedValue(initial);
    const user = userEvent.setup();
    const first = render(<LabelingPage />);
    await first.findByRole("button", { name: "n0.jpg" });

    await user.click(screen.getByRole("button", { name: /其余 1 条/ }));
    expect(screen.getByRole("button", { name: "n4.jpg" })).toBeInTheDocument();
    first.unmount();

    render(<LabelingPage />);
    expect(await screen.findByRole("button", { name: "n4.jpg" })).toBeInTheDocument();
  });
});
