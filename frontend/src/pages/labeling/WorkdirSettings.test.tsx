import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { api } from "../../api";
import { WorkdirSettings } from "./WorkdirSettings";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: {
    getWorkdir: vi.fn(),
    getWorkdirStats: vi
      .fn()
      .mockResolvedValue({ asset_count: 4, asset_bytes: 2097152 }),
    listBatches: vi.fn(),
    setBatchActive: vi.fn(),
    updateBatch: vi.fn(),
    retryRelocationCleanup: vi.fn(),
    currentRun: vi.fn().mockResolvedValue({ status: "completed" }),
    relocationStatus: vi.fn().mockResolvedValue([]),
    previewProductCleanup: vi.fn().mockResolvedValue({ products: [], total_bytes: 0 }),
    previewRunCleanup: vi.fn().mockResolvedValue([]),
  },
}));

it("隐藏策略等待确认，失败保留活跃状态并可重试成功", async () => {
  const user = userEvent.setup();
  const changed = vi.fn();
  const batch = {
    id: "s1",
    seq: 1,
    name: "详细描述",
    description: "",
    active: true,
    created_at: "2026-09-17T00:00:00Z",
    product_count: 3,
  };
  vi.mocked(api.getWorkdir).mockResolvedValue({
    id: "w1",
    path: "/srv/dataset",
    title: "素材",
    last_used_at: 0,
  });
  vi.mocked(api.listBatches).mockResolvedValue([batch]);
  vi.mocked(api.setBatchActive).mockRejectedValueOnce(new Error("目录暂时被占用"));
  render(<WorkdirSettings wid="w1" onBack={vi.fn()} onChanged={changed} />);

  await user.click(await screen.findByRole("button", { name: "停用" }));
  const dialog = within(screen.getByRole("dialog", { name: "隐藏策略？" }));
  expect(dialog.getByText(/已完成条目与产物保留/)).toBeVisible();
  expect(api.setBatchActive).not.toHaveBeenCalled();
  await user.click(dialog.getByRole("button", { name: "确认" }));

  expect(await dialog.findByRole("alert")).toHaveTextContent("目录暂时被占用");
  expect(api.setBatchActive).toHaveBeenCalledExactlyOnceWith("w1", "s1", false);
  expect(screen.getByText("活跃")).toBeVisible();
  expect(changed).not.toHaveBeenCalled();

  vi.mocked(api.setBatchActive).mockResolvedValue({ ...batch, active: false });
  vi.mocked(api.listBatches).mockResolvedValue([{ ...batch, active: false }]);
  await user.click(dialog.getByRole("button", { name: "确认" }));

  expect(await screen.findByRole("button", { name: "启用" })).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(changed).toHaveBeenCalledOnce();
});

it("运行中的策略显示运行状态并禁止删除，仍可请求隐藏", async () => {
  vi.mocked(api.getWorkdir).mockResolvedValue({
    id: "w1",
    path: "/srv/dataset",
    title: "素材",
    last_used_at: 0,
  });
  vi.mocked(api.listBatches).mockResolvedValue([
    {
      id: "s1",
      seq: 1,
      name: "描述",
      description: "",
      active: true,
      created_at: "",
      product_count: 0,
    },
  ]);
  vi.mocked(api.currentRun).mockResolvedValueOnce({
    run_id: "run1",
    batch: 1,
    status: "running",
    mode: "full",
    counters: {},
    current_item: null,
    error: null,
  });
  render(<WorkdirSettings wid="w1" onBack={vi.fn()} onChanged={vi.fn()} />);

  expect(await screen.findByText("运行中")).toBeVisible();
  expect(screen.getByRole("button", { name: "删除策略 s1" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "停用" })).toBeEnabled();
});

it("取消就地改名不写盘", async () => {
  const user = userEvent.setup();
  const batch = {
    id: "s1",
    seq: 1,
    name: "详细描述",
    description: "",
    active: true,
    created_at: "",
    product_count: 0,
  };
  vi.mocked(api.getWorkdir).mockResolvedValue({
    id: "w1",
    path: "/srv/dataset",
    title: "素材",
    last_used_at: 0,
  });
  vi.mocked(api.listBatches).mockResolvedValue([batch]);
  render(<WorkdirSettings wid="w1" onBack={vi.fn()} onChanged={vi.fn()} />);

  await user.click(await screen.findByRole("button", { name: "改名策略 详细描述" }));
  const input = screen.getByRole("textbox", { name: "策略名称 s1" });
  await user.clear(input);
  await user.type(input, "未提交{Escape}");

  expect(
    await screen.findByRole("button", { name: "改名策略 详细描述" }),
  ).toBeVisible();
  expect(api.updateBatch).not.toHaveBeenCalled();
});

it("统计读取失败不阻断维护入口，也不展示零值代替失败", async () => {
  vi.mocked(api.getWorkdir).mockResolvedValue({
    id: "w1",
    path: "/srv/dataset",
    title: "素材",
    last_used_at: 0,
  });
  vi.mocked(api.listBatches).mockResolvedValue([]);
  vi.mocked(api.getWorkdirStats).mockRejectedValueOnce(new Error("统计读取失败"));

  render(<WorkdirSettings wid="w1" onBack={vi.fn()} onChanged={vi.fn()} />);

  expect(await screen.findByText("统计读取失败")).toBeVisible();
  expect(screen.getByRole("button", { name: "导入素材" })).toBeEnabled();
  expect(screen.getByRole("region", { name: "基本信息" })).not.toHaveTextContent(
    "素材 0",
  );
});

it("离开设置页后完成的清理请求不触发刷新", async () => {
  const user = userEvent.setup();
  const changed = vi.fn();
  let resolveCleanup:
    | ((value: { old_path: string; cleanup_pending: boolean }) => void)
    | undefined;
  vi.mocked(api.getWorkdir).mockResolvedValue({
    id: "w1",
    path: "/srv/dataset",
    title: "素材",
    last_used_at: 0,
  });
  vi.mocked(api.listBatches).mockResolvedValue([]);
  vi.mocked(api.retryRelocationCleanup).mockReturnValue(
    new Promise((resolve) => {
      resolveCleanup = resolve;
    }),
  );
  vi.mocked(api.relocationStatus).mockResolvedValue([
    { old_path: "/srv/old", path: "/srv/dataset", status: "cleanup-pending" },
  ]);
  const { unmount } = render(
    <WorkdirSettings wid="w1" onBack={vi.fn()} onChanged={changed} />,
  );
  await user.click(await screen.findByRole("button", { name: "重试清理旧位置" }));
  unmount();
  resolveCleanup?.({ old_path: "/srv/old", cleanup_pending: false });

  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(changed).not.toHaveBeenCalled();
});

it("点击策略名就地编辑，失败保留输入，回车重试成功后更新目录", async () => {
  const user = userEvent.setup();
  const changed = vi.fn();
  const batch = {
    id: "s1",
    seq: 1,
    name: "详细描述",
    description: "",
    active: true,
    created_at: "",
    product_count: 2,
  };
  vi.mocked(api.getWorkdir).mockResolvedValue({
    id: "w1",
    path: "/srv/dataset",
    title: "素材",
    last_used_at: 0,
  });
  vi.mocked(api.listBatches).mockResolvedValue([batch]);
  vi.mocked(api.updateBatch).mockRejectedValueOnce(new Error("保存失败"));
  render(<WorkdirSettings wid="w1" onBack={vi.fn()} onChanged={changed} />);

  await user.click(await screen.findByRole("button", { name: "改名策略 详细描述" }));
  const input = screen.getByRole("textbox", { name: "策略名称 s1" });
  await user.clear(input);
  await user.type(input, "新描述{Enter}");

  expect(await screen.findByRole("alert")).toHaveTextContent("保存失败");
  expect(input).toHaveValue("新描述");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(changed).not.toHaveBeenCalled();
  vi.mocked(api.updateBatch).mockResolvedValue({ ...batch, name: "新描述" });
  vi.mocked(api.listBatches).mockResolvedValue([{ ...batch, name: "新描述" }]);
  await user.type(input, "{Enter}");

  expect(await screen.findByRole("button", { name: "改名策略 新描述" })).toBeVisible();
  expect(api.updateBatch).toHaveBeenLastCalledWith("w1", "s1", { name: "新描述" });
  expect(changed).toHaveBeenCalledOnce();
});

it("清理摘要按策略去重统计，并区分已停用策略", async () => {
  vi.mocked(api.getWorkdir).mockResolvedValue({
    id: "w1",
    path: "/srv/dataset",
    title: "素材",
    last_used_at: 0,
  });
  vi.mocked(api.listBatches).mockResolvedValue([
    {
      id: "s1",
      seq: 1,
      name: "详细描述",
      description: "",
      active: true,
      created_at: "2026-09-17T00:00:00Z",
      product_count: 2,
    },
    {
      id: "s2",
      seq: 2,
      name: "简短描述",
      description: "",
      active: false,
      created_at: "2026-09-17T00:00:00Z",
      product_count: 1,
    },
  ]);
  vi.mocked(api.previewProductCleanup).mockResolvedValue({
    products: [
      { name: "s1__first.txt", size: 1024, batch: 1 },
      { name: "s1__second.txt", size: 1024, batch: 1 },
      { name: "s2__third.txt", size: 1024, batch: 2 },
    ],
    total_bytes: 3072,
  });
  vi.mocked(api.previewRunCleanup).mockResolvedValue([
    {
      name: "later",
      size: 1024,
      modified_at: new Date(2026, 8, 12, 12).getTime() / 1000,
    },
    {
      name: "earlier",
      size: 2048,
      modified_at: new Date(2026, 8, 9, 12).getTime() / 1000,
    },
  ]);

  render(<WorkdirSettings wid="w1" onBack={vi.fn()} onChanged={vi.fn()} />);

  const cleanup = within(await screen.findByRole("region", { name: "清理" }));
  expect(
    cleanup.getByText("3 个 txt（2 套策略，含 1 套已停用）· 3.0 KiB"),
  ).toBeVisible();
  expect(screen.getByRole("region", { name: "基本信息" })).toHaveTextContent(
    "素材 4 · 产物 3 · 2.00 MiB",
  );
  expect(screen.getByText("产物 2 / 4")).toBeVisible();
  expect(cleanup.getByText("2 份 · 3.0 KiB · 2026-09-09 → 2026-09-12")).toBeVisible();
});

it("区块顺序与原型一致：基本信息后先清理再策略与危险操作", async () => {
  vi.mocked(api.getWorkdir).mockResolvedValue({
    id: "w1",
    path: "/srv/dataset",
    title: "素材",
    last_used_at: 0,
  });
  vi.mocked(api.listBatches).mockResolvedValue([]);

  render(<WorkdirSettings wid="w1" onBack={vi.fn()} onChanged={vi.fn()} />);

  await screen.findByRole("region", { name: "基本信息" });
  const regions = within(screen.getByRole("region", { name: "工作目录设置" }))
    .getAllByRole("region")
    .map((region) => region.getAttribute("aria-label"));
  expect(regions).toEqual(["基本信息", "清理", "目录策略", "危险操作"]);
});
