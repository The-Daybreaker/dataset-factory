import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../../api";
import type { components } from "../../api-types.gen";
import { RunOverview } from "./RunOverview";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: { latestRun: vi.fn(), readRunText: vi.fn() },
}));

const history: components["schemas"]["RunHistoryView"] = {
  record: {
    run_id: "run1",
    batch: 1,
    mode: "retry",
    trigger: "cli",
    strategy_hash: "hash1",
    snapshot: "strategies/s1.json",
    dsf_version: "0.1.0",
    status: "interrupted",
    counters: { planned: 3, attempted: 1, succeeded: 1, failed: 0, skipped: 0 },
    started_at: "2026-09-17T00:00:00Z",
    finished_at: "2026-09-17T00:00:02Z",
  },
  log_path: "/work/.dsf/runs/run1/run.log",
  items_path: "/work/.dsf/runs/run1/items.jsonl",
};
const fallback = { total: 100, done: 98, failed: 2 };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.latestRun).mockResolvedValue(history);
  vi.mocked(api.readRunText).mockResolvedValue({
    path: history.log_path ?? "",
    text: "运行已中断",
  });
});

it("最近一次重试的统计不能用整个批次的素材数替代，日志固定到该运行", async () => {
  const user = userEvent.setup();
  render(<RunOverview wid="work" batch="s1" refreshKey={0} fallback={fallback} />);
  const region = await screen.findByRole("region", { name: "本次运行" });
  expect(within(region).getByText("总数").nextElementSibling).toHaveTextContent("1");
  expect(within(region).getByText(/未跑 2 条/)).toBeInTheDocument();
  expect(within(region).getByText("CLI")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "查看日志" }));
  expect(await screen.findByText("运行已中断")).toBeInTheDocument();
  expect(api.readRunText).toHaveBeenCalledExactlyOnceWith(
    "work",
    "s1",
    "run1",
    "run.log",
  );
});

it("切换批次丢弃旧批次的迟到运行摘要", async () => {
  let finish: ((value: typeof history) => void) | undefined;
  vi.mocked(api.latestRun).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { rerender } = render(
    <RunOverview wid="work" batch="s1" refreshKey={0} fallback={fallback} />,
  );
  vi.mocked(api.latestRun).mockResolvedValue({
    record: null,
    log_path: null,
    items_path: null,
  });
  rerender(<RunOverview wid="work" batch="s2" refreshKey={0} fallback={fallback} />);
  await act(async () => {
    finish?.(history);
  });
  expect(screen.queryByRole("region", { name: "本次运行" })).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "条目汇总" })).toBeInTheDocument();
});

it("读取失败给出重查入口，不将错误当作空记录", async () => {
  const user = userEvent.setup();
  vi.mocked(api.latestRun).mockRejectedValueOnce(new Error("磁盘读取失败"));
  render(<RunOverview wid="work" batch="s1" refreshKey={0} fallback={fallback} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("磁盘读取失败");
  await user.click(screen.getByRole("button", { name: "重新查询" }));
  expect(await screen.findByRole("region", { name: "本次运行" })).toBeInTheDocument();
});

it("运行结束后短暂维护占用会自动重查，成功后清除错误", async () => {
  vi.mocked(api.latestRun).mockRejectedValueOnce(
    new ApiError("http", "工作目录正在维护", 409, null),
  );
  render(<RunOverview wid="work" batch="s1" refreshKey={0} fallback={fallback} />);

  expect(await screen.findByRole("alert")).toHaveTextContent("工作目录正在维护");

  expect(await screen.findByRole("region", { name: "本次运行" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(api.latestRun).toHaveBeenCalledTimes(2);
});

it("日志视图显示尾部 200 行且最新在最上", async () => {
  const user = userEvent.setup();
  const lines = Array.from({ length: 260 }, (_, index) => `第 ${index + 1} 行`);
  vi.mocked(api.readRunText).mockResolvedValue({ path: "p", text: lines.join("\n") });
  render(<RunOverview wid="work" batch="s1" refreshKey={0} fallback={fallback} />);
  await screen.findByRole("region", { name: "本次运行" });
  await user.click(screen.getByRole("button", { name: "查看日志" }));
  const pre = await screen.findByTestId("run-log-body");
  expect(pre.textContent?.startsWith("第 260 行")).toBe(true);
  expect(pre?.textContent).toContain("第 61 行");
  expect(pre?.textContent).not.toContain("第 60 行");
  expect(pre?.textContent?.split("\n")).toHaveLength(200);
});

it("逐条流水切换为按条目聚合的表格并重新取数", async () => {
  const user = userEvent.setup();
  const records = [
    {
      item: "a.png",
      status: "succeeded",
      attempt: 1,
      elapsed_ms: 1800,
      asset_hash: "3f9a1cdef",
    },
    {
      item: "b.png",
      status: "failed",
      attempt: 3,
      elapsed_ms: 300,
      message: "不支持的图片格式",
    },
    {
      item: "a.png",
      status: "succeeded",
      attempt: 2,
      elapsed_ms: 2000,
      asset_hash: "aaaaaa11",
    },
  ]
    .map((record) => JSON.stringify(record))
    .join("\n");
  vi.mocked(api.readRunText).mockImplementation(async (_wid, _batch, _runId, file) =>
    file === "run.log" ? { path: "p", text: "ok" } : { path: "q", text: records },
  );
  render(<RunOverview wid="work" batch="s1" refreshKey={0} fallback={fallback} />);
  await screen.findByRole("region", { name: "本次运行" });
  await user.click(screen.getByRole("button", { name: "查看日志" }));
  await user.click(await screen.findByRole("tab", { name: "逐条流水" }));
  expect(api.readRunText).toHaveBeenLastCalledWith("work", "s1", "run1", "items.jsonl");
  const rows = within(screen.getByRole("table")).getAllByRole("row");
  expect(rows).toHaveLength(3);
  const rowA = rows[1];
  const rowB = rows[2];
  if (!rowA || !rowB) throw new Error("聚合表行缺失");
  expect(rowA).toHaveTextContent("a.png");
  expect(within(rowA).getByText("成功")).toBeInTheDocument();
  expect(rowA).toHaveTextContent("aaaaaa…");
  expect(rowB).toHaveTextContent("b.png");
  expect(within(rowB).getByText("失败")).toBeInTheDocument();
  expect(within(rowB).getByText("不支持的图片格式")).toBeInTheDocument();
});

it("复制把当前显示的内容写入剪贴板并短暂确认", async () => {
  const user = userEvent.setup();
  const writeText = vi.fn<(text: string) => Promise<void>>().mockResolvedValue();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
  render(<RunOverview wid="work" batch="s1" refreshKey={0} fallback={fallback} />);
  await screen.findByRole("region", { name: "本次运行" });
  await user.click(screen.getByRole("button", { name: "查看日志" }));
  await user.click(await screen.findByRole("button", { name: "复制" }));
  expect(writeText).toHaveBeenCalledWith("运行已中断");
  expect(await screen.findByText("已复制")).toBeInTheDocument();
});
