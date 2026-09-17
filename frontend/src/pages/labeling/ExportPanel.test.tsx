import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../../api";
import type { components } from "../../api-types.gen";
import { ExportPanel } from "./ExportPanel";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: {
    exportPlan: vi.fn(),
    startExport: vi.fn(),
    setExclusions: vi.fn(),
    getTask: vi.fn(),
    cancelTask: vi.fn(),
  },
}));

const row: components["schemas"]["ExportPlanRow"] = {
  item: "photo",
  name: "photo.jpg",
  asset_name: "001.jpg",
  caption_name: "001.txt",
  asset_bytes: 1024,
  caption_bytes: 32,
  integrity: "changed",
};
const plan: components["schemas"]["ExportPlanView"] = {
  batch: 1,
  included: [row],
  excluded: [{ ...row, item: "missing", name: "missing.jpg", reason: "缺失" }],
  total_bytes: 1056,
  sequential: true,
  non_ascii_names: false,
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.exportPlan).mockResolvedValue(plan);
  vi.mocked(api.setExclusions).mockResolvedValue({ id: "s1", seq: 1, items: [] });
  vi.mocked(api.startExport).mockResolvedValue({ task_id: "export-task" });
});

it("取消请求受理后继续查询原任务，取消终态不提供下载", async () => {
  const user = userEvent.setup();
  const running = {
    id: "export-task",
    status: "running" as const,
    progress: 0.3,
    result: null,
    error: null,
  };
  vi.mocked(api.getTask).mockResolvedValue(running);
  vi.mocked(api.cancelTask).mockResolvedValue(running);
  render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");
  await user.click(screen.getByRole("button", { name: "导出当前策略" }));
  await screen.findByText("正在打包 · 30%");
  vi.mocked(api.getTask).mockResolvedValue({ ...running, status: "cancelled" });

  await user.click(screen.getByRole("button", { name: "取消导出" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("导出已取消");
  expect(api.cancelTask).toHaveBeenCalledExactlyOnceWith("export-task");
  expect(api.startExport).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("link", { name: "下载 ZIP" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "取消导出" })).not.toBeInTheDocument();
});

it("取消失败保留运行和原句柄，可再次取消", async () => {
  const user = userEvent.setup();
  const running = {
    id: "export-task",
    status: "running" as const,
    progress: 0.3,
    result: null,
    error: null,
  };
  vi.mocked(api.getTask).mockResolvedValue(running);
  vi.mocked(api.cancelTask)
    .mockRejectedValueOnce(new Error("取消请求网络中断"))
    .mockResolvedValue(running);
  render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");
  await user.click(screen.getByRole("button", { name: "导出当前策略" }));
  await screen.findByText("正在打包 · 30%");

  await user.click(screen.getByRole("button", { name: "取消导出" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("取消请求网络中断");
  expect(screen.getByRole("button", { name: "导出当前策略" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "取消导出" }));
  expect(await screen.findByText("正在取消 · 30%")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "取消导出" })).toBeDisabled();
  expect(api.cancelTask).toHaveBeenCalledTimes(2);
  expect(api.startExport).toHaveBeenCalledTimes(1);
});

it("切换批次后迟到的取消失败不污染新批次", async () => {
  const user = userEvent.setup();
  let rejectCancel: ((reason: Error) => void) | undefined;
  vi.mocked(api.getTask).mockResolvedValue({
    id: "export-task",
    status: "running",
    progress: 0.3,
    result: null,
    error: null,
  });
  vi.mocked(api.cancelTask).mockImplementation(
    () =>
      new Promise((_resolve, reject) => {
        rejectCancel = reject;
      }),
  );
  const { rerender } = render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");
  await user.click(screen.getByRole("button", { name: "导出当前策略" }));
  await screen.findByText("正在打包 · 30%");
  await user.click(screen.getByRole("button", { name: "取消导出" }));

  rerender(<ExportPanel wid="work" batch="s2" refreshKey={0} />);
  await act(async () => {
    rejectCancel?.(new Error("旧批次取消失败"));
  });

  await screen.findByText("001.jpg + 001.txt");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "导出当前策略" })).toBeEnabled();
});

it("默认顺序命名、变更素材仍入包，关闭后展示原名与非 ASCII 风险", async () => {
  const user = userEvent.setup();
  render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  expect(await screen.findByText("001.jpg + 001.txt")).toBeInTheDocument();
  expect(screen.getByRole("switch")).toBeChecked();
  expect(
    within(screen.getByRole("region", { name: "将入包清单" })).getByText(
      "打标后素材已变更",
    ),
  ).toBeInTheDocument();
  vi.mocked(api.exportPlan).mockResolvedValue({
    ...plan,
    sequential: false,
    non_ascii_names: true,
    included: [{ ...row, asset_name: "照片.jpg", caption_name: "照片.txt" }],
  });

  await user.click(screen.getByRole("switch"));

  expect(api.exportPlan).toHaveBeenLastCalledWith("work", "s1", false);
  expect(await screen.findByText("照片.jpg + 照片.txt")).toBeInTheDocument();
  expect(screen.getByText(/跨平台解压可能导致/)).toBeInTheDocument();
});

it("打包成功先于取消失败返回时保留下载结果", async () => {
  const user = userEvent.setup();
  let rejectCancel: ((reason: Error) => void) | undefined;
  const url = "/api/workdirs/work/export/files/s1-0123456789abcdef01234567.zip";
  vi.mocked(api.getTask).mockRejectedValueOnce(new Error("查询中断"));
  vi.mocked(api.cancelTask).mockImplementation(
    () =>
      new Promise((_resolve, reject) => {
        rejectCancel = reject;
      }),
  );
  render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");
  await user.click(screen.getByRole("button", { name: "导出当前策略" }));
  await screen.findByText("查询中断");
  await user.click(screen.getByRole("button", { name: "取消导出" }));
  vi.mocked(api.getTask).mockResolvedValue({
    id: "export-task",
    status: "succeeded",
    progress: 1,
    result: { path: "/output.zip", download_url: url },
    error: null,
  });

  await user.click(screen.getByRole("button", { name: "重新查询" }));
  await screen.findByRole("link", { name: "下载 ZIP" });
  await act(async () => {
    rejectCancel?.(new Error("迟到取消错误"));
  });

  expect(screen.getByRole("link", { name: "下载 ZIP" })).toHaveAttribute("href", url);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText(/正在取消|正在打包/)).not.toBeInTheDocument();
  expect(api.startExport).toHaveBeenCalledTimes(1);
});

it("批量排除后重读服务器计划，撤销只作用于手动排除条目", async () => {
  const user = userEvent.setup();
  render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");
  const included = within(screen.getByRole("region", { name: "将入包清单" }));
  await user.click(included.getByRole("button", { name: "选择" }));
  await user.click(included.getByRole("button", { name: "全选" }));
  vi.mocked(api.exportPlan).mockResolvedValue({
    ...plan,
    included: [],
    excluded: [...plan.excluded, { ...row, reason: "用户排除" }],
  });

  await user.click(included.getByRole("button", { name: "排除选中的" }));

  expect(api.setExclusions).toHaveBeenCalledWith("work", "s1", ["photo"], true);
  await screen.findByText("用户排除");
  const excluded = within(screen.getByRole("region", { name: "被排除清单" }));
  await user.click(excluded.getByRole("button", { name: "选择" }));
  expect(excluded.getAllByRole("checkbox")).toHaveLength(1);
  await user.click(excluded.getByRole("button", { name: "全选" }));
  vi.mocked(api.exportPlan).mockResolvedValue(plan);
  const undo = excluded.getAllByRole("button", { name: "撤销排除" })[0];
  if (!undo) throw new Error("缺少撤销按钮");
  await user.click(undo);
  expect(api.setExclusions).toHaveBeenLastCalledWith("work", "s1", ["photo"], false);
  expect(await screen.findByText("001.jpg + 001.txt")).toBeInTheDocument();
});

it("后台刷新计划保留选择模式与仍然有效的勾选，刷新期间不能操作旧数据", async () => {
  const user = userEvent.setup();
  const { rerender } = render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");
  const included = within(screen.getByRole("region", { name: "将入包清单" }));
  await user.click(included.getByRole("button", { name: "选择" }));
  await user.click(included.getByRole("checkbox", { name: "选择 photo.jpg" }));
  let resolvePlan: ((value: typeof plan) => void) | undefined;
  vi.mocked(api.exportPlan).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolvePlan = resolve;
      }),
  );

  rerender(<ExportPanel wid="work" batch="s1" refreshKey={1} />);

  expect(screen.queryByRole("region", { name: "将入包清单" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "导出当前策略" })).toBeDisabled();
  await act(async () => {
    resolvePlan?.(plan);
  });
  expect(included.getByRole("button", { name: "退出选择" })).toBeVisible();
  expect(included.getByRole("checkbox", { name: "选择 photo.jpg" })).toBeChecked();
  await user.click(included.getByRole("button", { name: "排除选中的" }));
  expect(api.setExclusions).toHaveBeenCalledExactlyOnceWith(
    "work",
    "s1",
    ["photo"],
    true,
  );
});

it("任务查询失败可重查原句柄，成功后才出现下载链接", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getTask).mockRejectedValueOnce(new Error("网络中断"));
  render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");
  await user.click(screen.getByRole("button", { name: "导出当前策略" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("网络中断");
  expect(screen.queryByRole("link", { name: "下载 ZIP" })).not.toBeInTheDocument();
  const url = "/api/workdirs/work/export/files/s1-0123456789abcdef01234567.zip";
  vi.mocked(api.getTask).mockResolvedValue({
    id: "export-task",
    status: "succeeded",
    progress: 1,
    result: { path: "/output.zip", download_url: url },
    error: null,
  });

  await user.click(screen.getByRole("button", { name: "重新查询" }));

  expect(await screen.findByRole("link", { name: "下载 ZIP" })).toHaveAttribute(
    "href",
    url,
  );
  expect(api.startExport).toHaveBeenCalledExactlyOnceWith("work", "s1", true);
  expect(api.getTask).toHaveBeenLastCalledWith("export-task");
});

it("空任务编号不能进入无法查询的打包状态，重查后可重新导出", async () => {
  const user = userEvent.setup();
  vi.mocked(api.startExport).mockResolvedValueOnce({ task_id: "" });
  render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");

  await user.click(screen.getByRole("button", { name: "导出当前策略" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("导出任务编号无效");
  expect(screen.queryByText(/正在打包/)).not.toBeInTheDocument();
  expect(api.getTask).not.toHaveBeenCalled();
  expect(screen.queryByRole("link", { name: "下载 ZIP" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "重新查询" }));
  await screen.findByText("001.jpg + 001.txt");
  expect(screen.getByRole("button", { name: "导出当前策略" })).toBeEnabled();
  expect(api.startExport).toHaveBeenCalledTimes(1);
});

it("切换批次丢弃迟到计划和原选择", async () => {
  let resolveOld: ((value: typeof plan) => void) | undefined;
  vi.mocked(api.exportPlan).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
  );
  const { rerender } = render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  vi.mocked(api.exportPlan).mockResolvedValue({ ...plan, batch: 2, included: [] });
  rerender(<ExportPanel wid="work" batch="s2" refreshKey={0} />);

  await act(async () => {
    resolveOld?.(plan);
  });

  expect(screen.queryByText("001.jpg + 001.txt")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "导出当前策略" })).toBeDisabled();
});

it("任务丢失后必须重查计划，且不会自动重新提交导出", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getTask).mockRejectedValue(
    new ApiError("http", "任务不存在", 404, null),
  );
  const { rerender } = render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");
  await user.click(screen.getByRole("button", { name: "导出当前策略" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("导出任务已丢失");

  rerender(<ExportPanel wid="work" batch="s1" refreshKey={1} />);
  await screen.findByText("001.jpg + 001.txt");
  expect(screen.getByRole("alert")).toHaveTextContent("导出任务已丢失");
  expect(screen.getByRole("button", { name: "导出当前策略" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "重新查询" }));
  await screen.findByText("001.jpg + 001.txt");

  expect(screen.getByRole("button", { name: "导出当前策略" })).toBeEnabled();
  expect(api.startExport).toHaveBeenCalledTimes(1);
  expect(api.getTask).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("link", { name: "下载 ZIP" })).not.toBeInTheDocument();
});

it("计划刷新失败后不允许操作旧清单，重查成功才恢复", async () => {
  const user = userEvent.setup();
  const { rerender } = render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");
  vi.mocked(api.exportPlan).mockRejectedValueOnce(new Error("计划读取失败"));

  rerender(<ExportPanel wid="work" batch="s1" refreshKey={1} />);

  expect(await screen.findByRole("alert")).toHaveTextContent("计划读取失败");
  expect(screen.queryByText("001.jpg + 001.txt")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "导出当前策略" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "重查导出计划" }));
  await screen.findByText("001.jpg + 001.txt");
  expect(screen.getByRole("button", { name: "导出当前策略" })).toBeEnabled();
});

it.each([
  [null, "导出结果格式无效"],
  [{ path: "/output.zip", download_url: "/wrong.zip" }, "导出下载地址无效"],
])("成功载荷无效时保留原任务供重查：%j", async (result, message) => {
  const user = userEvent.setup();
  vi.mocked(api.getTask).mockResolvedValueOnce({
    id: "export-task",
    status: "succeeded",
    progress: 1,
    result,
    error: null,
  });
  render(<ExportPanel wid="work" batch="s1" refreshKey={0} />);
  await screen.findByText("001.jpg + 001.txt");
  await user.click(screen.getByRole("button", { name: "导出当前策略" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.queryByRole("link", { name: "下载 ZIP" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "导出当前策略" })).toBeDisabled();
  const url = "/api/workdirs/work/export/files/s1-0123456789abcdef01234567.zip";
  vi.mocked(api.getTask).mockResolvedValue({
    id: "export-task",
    status: "succeeded",
    progress: 1,
    result: { path: "/output.zip", download_url: url },
    error: null,
  });

  await user.click(screen.getByRole("button", { name: "重新查询" }));

  expect(await screen.findByRole("link", { name: "下载 ZIP" })).toHaveAttribute(
    "href",
    url,
  );
  expect(api.getTask).toHaveBeenCalledTimes(2);
  expect(api.getTask).toHaveBeenLastCalledWith("export-task");
  expect(api.startExport).toHaveBeenCalledExactlyOnceWith("work", "s1", true);
});
