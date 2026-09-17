import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import type { components } from "../../api-types.gen";
import { LabelingPage } from "./LabelingPage";

vi.mock("../../api", () => ({
  api: {
    listWorkdirs: vi.fn(),
    listBatches: vi.fn(),
    listItems: vi.fn(),
    readCaption: vi.fn().mockResolvedValue("caption"),
    addRetryItems: vi.fn(),
    removeRetryItem: vi.fn(),
    clearRetryItems: vi.fn(),
    importMaterials: vi.fn(),
    removeUnimported: vi.fn(),
    getTask: vi.fn(),
    cancelTask: vi.fn(),
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
});

describe("打标页读取流程", () => {
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
    expect(video.parentElement).toHaveClass("h-[76px]", "w-full");
    await user.click(screen.getByRole("button", { name: "展开素材" }));
    expect(screen.getByLabelText("clip.mp4")).toBe(video);
    expect(video.currentTime).toBe(12);
    expect(video.parentElement).toHaveClass("h-96", "w-full");
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
    await user.click(await screen.findByRole("button", { name: "清空列表" }));
    expect(api.clearRetryItems).toHaveBeenCalledWith("one", "s1");
    expect(screen.queryByRole("button", { name: "清空列表" })).not.toBeInTheDocument();
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

    await user.click(await screen.findByRole("button", { name: "清空列表" }));

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
    await user.type(screen.getByRole("textbox", { name: "搜索素材" }), "absent");
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
