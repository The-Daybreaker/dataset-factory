import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import type { components } from "../api-types.gen";
import { DirectoryPicker } from "./DirectoryPicker";

vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  api: {
    listDirectory: vi.fn(),
    renameDirectory: vi.fn(),
    createDirectory: vi.fn(),
    filesystemCapabilities: vi.fn(),
    openDirectory: vi.fn(),
    getTask: vi.fn(),
    cancelTask: vi.fn(),
  },
}));

const listing: components["schemas"]["DirectoryListing"] = {
  hostname: "test-server",
  system: "Linux",
  path: "/srv",
  parent: "/",
  unavailable_count: 0,
  entries: [
    {
      name: "package",
      path: "/srv/package",
      kind: "directory",
      modified_at: "2026-09-17T00:00:00Z",
      size: null,
    },
    {
      name: "SKILL.md",
      path: "/srv/SKILL.md",
      kind: "file",
      modified_at: "2026-09-17T00:00:00Z",
      size: 1024,
    },
  ],
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listDirectory).mockResolvedValue(listing);
  vi.mocked(api.filesystemCapabilities).mockResolvedValue({
    open_in_file_manager: false,
  });
});

it("只读浏览仅在后端自报支持时显示系统打开按钮", async () => {
  const user = userEvent.setup();
  vi.mocked(api.filesystemCapabilities).mockResolvedValue({
    open_in_file_manager: true,
  });
  vi.mocked(api.openDirectory).mockResolvedValue(undefined);
  render(<DirectoryPicker browseOnly onClose={vi.fn()} onSelect={vi.fn()} />);
  await screen.findByText("当前后端：test-server · Linux");

  await user.click(
    await screen.findByRole("button", { name: "在系统文件资源管理器中打开" }),
  );

  expect(api.openDirectory).toHaveBeenCalledExactlyOnceWith("/srv");
});

it("重命名确认后才提交，查询失败重查原任务并采用新路径", async () => {
  const user = userEvent.setup();
  const selected = vi.fn();
  vi.mocked(api.renameDirectory).mockResolvedValue({ task_id: "rename-task" });
  vi.mocked(api.getTask).mockRejectedValueOnce(new Error("查询断线"));
  render(<DirectoryPicker allowRename onClose={vi.fn()} onSelect={selected} />);

  await user.click(await screen.findByRole("button", { name: "重命名 package" }));
  const name = screen.getByRole("textbox", { name: "重命名" });
  await user.clear(name);
  await user.type(name, "renamed");
  expect(api.renameDirectory).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "保存" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("查询断线");
  expect(screen.getByRole("button", { name: "选择此目录" })).toBeDisabled();
  vi.mocked(api.getTask).mockResolvedValue({
    id: "rename-task",
    status: "succeeded",
    progress: 1,
    error: null,
    result: { path: "/srv/renamed", cleanup_pending: false },
  });
  vi.mocked(api.listDirectory).mockResolvedValue({
    ...listing,
    path: "/srv/renamed",
    parent: "/srv",
    entries: [],
  });
  await user.click(screen.getByRole("button", { name: "重新查询" }));
  await screen.findByText("没有匹配的项目");
  await user.click(screen.getByRole("button", { name: "选择此目录" }));

  expect(api.renameDirectory).toHaveBeenCalledExactlyOnceWith(
    "/srv/package",
    "renamed",
  );
  expect(api.getTask).toHaveBeenLastCalledWith("rename-task");
  expect(selected).toHaveBeenCalledWith(
    "/srv/renamed",
    expect.objectContaining({ path: "/srv/renamed" }),
  );
});

it("重命名提交失败保留输入并允许重试", async () => {
  const user = userEvent.setup();
  vi.mocked(api.renameDirectory).mockRejectedValue(new Error("目录占用"));
  render(<DirectoryPicker allowRename onClose={vi.fn()} onSelect={vi.fn()} />);

  await user.click(await screen.findByRole("button", { name: "重命名 package" }));
  await user.type(screen.getByRole("textbox", { name: "重命名" }), "-new");
  await user.click(screen.getByRole("button", { name: "保存" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("目录占用");
  expect(screen.getByRole("textbox", { name: "重命名" })).toHaveValue("package-new");
  expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();
});

it("重命名任务丢失后刷新清单并释放关闭和选择", async () => {
  const user = userEvent.setup();
  vi.mocked(api.renameDirectory).mockResolvedValue({ task_id: "gone" });
  vi.mocked(api.getTask).mockRejectedValue(
    new ApiError("http", "任务不存在", 404, null),
  );
  render(<DirectoryPicker allowRename onClose={vi.fn()} onSelect={vi.fn()} />);

  await user.click(await screen.findByRole("button", { name: "重命名 package" }));
  await user.type(screen.getByRole("textbox", { name: "重命名" }), "-new");
  await user.click(screen.getByRole("button", { name: "保存" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("任务已丢失");
  expect(screen.getByRole("button", { name: "选择此目录" })).toBeEnabled();
  expect(screen.getByRole("button", { name: /^取消$/ })).toBeEnabled();
  expect(api.listDirectory).toHaveBeenCalledTimes(2);
});

it("取消重命名继续查询原任务直至终态，失败仍可重试取消", async () => {
  const user = userEvent.setup();
  const running = {
    id: "rename",
    status: "running" as const,
    progress: 0,
    error: null,
    result: null,
  };
  vi.mocked(api.renameDirectory).mockResolvedValue({ task_id: "rename" });
  vi.mocked(api.getTask).mockResolvedValue(running);
  vi.mocked(api.cancelTask).mockRejectedValueOnce(new Error("取消请求断线"));
  render(<DirectoryPicker allowRename onClose={vi.fn()} onSelect={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: "重命名 package" }));
  await user.type(screen.getByRole("textbox", { name: "重命名" }), "-new");
  await user.click(screen.getByRole("button", { name: "保存" }));

  await user.click(await screen.findByRole("button", { name: "取消重命名" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("取消请求断线");
  expect(screen.getByRole("button", { name: "选择此目录" })).toBeDisabled();
  vi.mocked(api.cancelTask).mockResolvedValue(running);
  vi.mocked(api.getTask).mockResolvedValue({
    ...running,
    status: "cancelled",
    error: "重命名已取消",
  });
  await user.click(screen.getByRole("button", { name: "取消重命名" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("重命名已取消");
  expect(screen.queryByRole("button", { name: "取消重命名" })).not.toBeInTheDocument();
  expect(api.cancelTask).toHaveBeenCalledTimes(2);
  expect(api.renameDirectory).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();
});

it("创建失败保留名称并释放重试按钮", async () => {
  const user = userEvent.setup();
  vi.mocked(api.createDirectory).mockRejectedValue(new Error("没有写入权限"));
  render(<DirectoryPicker allowCreate onClose={vi.fn()} onSelect={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: "新建目录" }));
  await user.type(screen.getByRole("textbox", { name: "目录名称" }), "new-parent");

  await user.click(screen.getByRole("button", { name: "创建" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("没有写入权限");
  expect(screen.getByRole("textbox", { name: "目录名称" })).toHaveValue("new-parent");
  expect(screen.getByRole("button", { name: "创建" })).toBeEnabled();
});

it("新建目录经服务器确认后进入新目录，确认选择前不回填", async () => {
  const user = userEvent.setup();
  const selected = vi.fn();
  vi.mocked(api.createDirectory).mockResolvedValue({ path: "/srv/new-parent" });
  render(<DirectoryPicker allowCreate onClose={vi.fn()} onSelect={selected} />);
  await screen.findByText("当前后端：test-server · Linux");

  await user.click(screen.getByRole("button", { name: "新建目录" }));
  await user.type(screen.getByRole("textbox", { name: "目录名称" }), "new-parent");
  expect(api.createDirectory).not.toHaveBeenCalled();
  vi.mocked(api.listDirectory).mockResolvedValue({
    ...listing,
    path: "/srv/new-parent",
    entries: [],
  });
  await user.click(screen.getByRole("button", { name: "创建" }));
  await screen.findByText("没有匹配的项目");

  expect(api.createDirectory).toHaveBeenCalledExactlyOnceWith("/srv", "new-parent");
  expect(selected).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "选择此目录" }));
  expect(selected).toHaveBeenCalledWith(
    "/srv/new-parent",
    expect.objectContaining({ path: "/srv/new-parent" }),
  );
});

it("初始目录响应不覆盖用户已输入的跳转路径", async () => {
  const user = userEvent.setup();
  let finish: ((value: typeof listing) => void) | undefined;
  vi.mocked(api.listDirectory).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<DirectoryPicker onClose={vi.fn()} onSelect={vi.fn()} />);
  const input = screen.getByRole("textbox", { name: "服务器路径" });

  await user.type(input, "/target");
  await act(async () => {
    finish?.(listing);
  });

  expect(input).toHaveValue("/target");
  expect(screen.getByText("当前后端：test-server · Linux")).toBeVisible();
  vi.mocked(api.listDirectory).mockResolvedValue({ ...listing, path: "/target" });
  await user.click(screen.getByRole("button", { name: "跳转" }));
  expect(api.listDirectory).toHaveBeenLastCalledWith("/target", false, false, []);
  expect(input).toHaveValue("/target");
});

it.each([
  {
    system: "Windows",
    path: "C:\\data\\images",
    root: "C:\\",
    parent: "C:\\data",
    label: "data",
  },
  {
    system: "Windows",
    path: "\\\\host\\share\\data\\images",
    root: "\\\\host\\share\\",
    parent: "\\\\host\\share\\data",
    label: "data",
  },
  {
    system: "Linux",
    path: "/data\\set/images",
    root: "/",
    parent: "/data\\set",
    label: "data\\set",
  },
])(
  "$system 面包屑保留服务器路径语义：$path",
  async ({ system, path, root, parent, label }) => {
    const user = userEvent.setup();
    vi.mocked(api.listDirectory).mockResolvedValue({
      ...listing,
      system,
      path,
      parent,
    });
    render(<DirectoryPicker initialPath={path} onClose={vi.fn()} onSelect={vi.fn()} />);
    await screen.findByText(`当前后端：test-server · ${system}`);
    const nav = within(screen.getByRole("navigation", { name: "目录路径" }));

    expect(nav.getAllByRole("button")).toHaveLength(3);
    expect(nav.getByRole("button", { name: root })).toBeVisible();
    await user.click(nav.getByRole("button", { name: label }));

    expect(api.listDirectory).toHaveBeenLastCalledWith(parent, false, false, []);
  },
);

it("读取后端身份并按指定后缀选文件，确认前不回填", async () => {
  const user = userEvent.setup();
  const selected = vi.fn();
  render(
    <DirectoryPicker
      initialPath="/srv"
      files
      suffixes={[".md", ".txt"]}
      onClose={vi.fn()}
      onSelect={selected}
    />,
  );
  expect(await screen.findByText("当前后端：test-server · Linux")).toBeVisible();
  expect(api.listDirectory).toHaveBeenCalledExactlyOnceWith("/srv", true, false, [
    ".md",
    ".txt",
  ]);

  await user.click(screen.getByRole("button", { name: "SKILL.md" }));

  expect(selected).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "选择此文件" }));
  expect(selected).toHaveBeenCalledExactlyOnceWith("/srv/SKILL.md", listing);
});

it("进入子目录后可选当前目录，返回父目录使用后端规范路径", async () => {
  const user = userEvent.setup();
  const selected = vi.fn();
  render(<DirectoryPicker initialPath="/srv" onClose={vi.fn()} onSelect={selected} />);
  await screen.findByRole("button", { name: "package" });
  vi.mocked(api.listDirectory).mockResolvedValue({
    ...listing,
    path: "/srv/package",
    parent: "/srv",
    entries: [],
  });

  await user.click(screen.getByRole("button", { name: "package" }));

  await screen.findByText("没有匹配的项目");
  await user.click(screen.getByRole("button", { name: "选择此目录" }));
  expect(selected).toHaveBeenCalledExactlyOnceWith(
    "/srv/package",
    expect.objectContaining({ path: "/srv/package" }),
  );
  await user.click(screen.getByRole("button", { name: "上一级" }));
  expect(api.listDirectory).toHaveBeenLastCalledWith("/srv", false, false, []);
});

it("跳转失败撤下旧清单与选择，输入修正后可重试", async () => {
  const user = userEvent.setup();
  render(<DirectoryPicker initialPath="/srv" onClose={vi.fn()} onSelect={vi.fn()} />);
  await screen.findByRole("button", { name: "package" });
  vi.mocked(api.listDirectory).mockRejectedValueOnce(new Error("目录不存在"));
  await user.clear(screen.getByRole("textbox", { name: "服务器路径" }));
  await user.type(screen.getByRole("textbox", { name: "服务器路径" }), "/gone");

  await user.click(screen.getByRole("button", { name: "跳转" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("目录不存在");
  expect(screen.queryByRole("button", { name: "package" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "选择此目录" })).toBeDisabled();
  await user.clear(screen.getByRole("textbox", { name: "服务器路径" }));
  await user.type(screen.getByRole("textbox", { name: "服务器路径" }), "/srv");
  await user.click(screen.getByRole("button", { name: "跳转" }));
  await screen.findByRole("button", { name: "package" });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("筛选变化后丢弃迟到结果，只读形态无文件选择动作", async () => {
  const user = userEvent.setup();
  let resolveOld: ((value: typeof listing) => void) | undefined;
  vi.mocked(api.listDirectory).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
  );
  const selected = vi.fn();
  render(
    <DirectoryPicker
      initialPath="/srv"
      browseOnly
      onClose={vi.fn()}
      onSelect={selected}
    />,
  );

  await user.click(screen.getByRole("checkbox", { name: "显示隐藏项" }));
  await screen.findByText("SKILL.md");
  await act(async () => {
    resolveOld?.({ ...listing, path: "/wrong", entries: [] });
  });

  expect(api.listDirectory).toHaveBeenLastCalledWith("/srv", true, true, []);
  expect(screen.getByRole("textbox", { name: "服务器路径" })).toHaveValue("/srv");
  expect(
    screen.queryByRole("button", { name: /选择此|SKILL.md|package/ }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("共 2 项 · 只读")).toBeVisible();
  expect(selected).not.toHaveBeenCalled();
});
