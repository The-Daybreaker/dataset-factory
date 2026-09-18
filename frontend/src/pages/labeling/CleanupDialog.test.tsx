import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api";
import { CleanupDialog } from "./CleanupDialog";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: {
    previewProductCleanup: vi.fn(),
    previewRunCleanup: vi.fn(),
    cleanupWorkdir: vi.fn(),
  },
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.previewProductCleanup).mockResolvedValue({
    products: [
      { name: "s1__lost.txt", size: 1024, batch: 1 },
      { name: "s2__other.txt", size: 2048, batch: 2 },
    ],
    total_bytes: 3072,
  });
});

it("清理只提交确认后的选择，失败保留选择，成功展示残留位置", async () => {
  const user = userEvent.setup();
  const cleaned = vi.fn();
  vi.mocked(api.cleanupWorkdir).mockRejectedValueOnce(new Error("运行中不可清理"));
  render(
    <CleanupDialog wid="w1" kind="products" onClose={vi.fn()} onCleaned={cleaned} />,
  );

  await user.click(await screen.findByRole("checkbox", { name: "s1__lost.txt" }));
  await user.click(screen.getByRole("button", { name: "清理（1）" }));
  expect(api.cleanupWorkdir).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "确认清理" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("运行中不可清理");
  expect(api.cleanupWorkdir).toHaveBeenCalledExactlyOnceWith("w1", "products", [
    "s1__lost.txt",
  ]);
  expect(screen.getByRole("checkbox", { name: "s1__lost.txt" })).toBeChecked();
  expect(cleaned).not.toHaveBeenCalled();
  vi.mocked(api.cleanupWorkdir).mockResolvedValue({
    count: 1,
    recovery_path: "/srv/.trash/cleanup",
  });
  await user.click(screen.getByRole("button", { name: "确认清理" }));

  expect(await screen.findByRole("status")).toHaveTextContent("/srv/.trash/cleanup");
  expect(cleaned).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "确认清理" })).not.toBeInTheDocument();
});

it("运行记录全选后清理准确名称，清理期间不可关闭", async () => {
  const user = userEvent.setup();
  const close = vi.fn();
  vi.mocked(api.previewRunCleanup).mockResolvedValue([
    { name: "run-a", size: 100, modified_at: 1700000000 },
    { name: "run-b", size: 200, modified_at: 1700000010 },
  ]);
  vi.mocked(api.cleanupWorkdir).mockImplementation(() => new Promise(() => {}));
  render(<CleanupDialog wid="w1" kind="runs" onClose={close} onCleaned={vi.fn()} />);

  await screen.findByRole("checkbox", { name: "run-a" });
  await user.click(screen.getByRole("checkbox", { name: "全选" }));
  await user.click(screen.getByRole("button", { name: "清理（2）" }));
  await user.click(screen.getByRole("button", { name: "确认清理" }));
  await user.keyboard("{Escape}");

  expect(api.cleanupWorkdir).toHaveBeenCalledExactlyOnceWith("w1", "runs", [
    "run-a",
    "run-b",
  ]);
  const closeButtons = screen.getAllByRole("button", { name: "关闭" });
  expect(closeButtons[0]).toBeDisabled();
  const cornerClose = closeButtons[1];
  if (!cornerClose) throw new Error("缺少弹窗关闭按钮");
  await user.click(cornerClose);
  expect(close).not.toHaveBeenCalled();
});

it("预览失败可重新读取且不执行清理", async () => {
  const user = userEvent.setup();
  vi.mocked(api.previewProductCleanup).mockRejectedValueOnce(new Error("目录读取失败"));
  render(
    <CleanupDialog wid="w1" kind="products" onClose={vi.fn()} onCleaned={vi.fn()} />,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent("目录读取失败");
  await user.click(screen.getByRole("button", { name: "重新读取" }));

  expect(
    await screen.findByRole("checkbox", { name: "s1__lost.txt" }),
  ).not.toBeChecked();
  expect(api.cleanupWorkdir).not.toHaveBeenCalled();
});

it("最近一次运行挂时效判定标记，其余运行与产物清单不挂", async () => {
  vi.mocked(api.previewRunCleanup).mockResolvedValue([
    { name: "run-a", size: 100, modified_at: 1700000000 },
    { name: "run-b", size: 200, modified_at: 1700000010 },
  ]);
  render(<CleanupDialog wid="w1" kind="runs" onClose={vi.fn()} onCleaned={vi.fn()} />);

  expect(await screen.findByText("run-a")).toBeInTheDocument();
  expect(screen.getByText("最近一次 · 当前产物时效判定用")).toBeInTheDocument();
  // 标记只出现一次，且挂在更晚的 run-b 那一行（同行的名字在标记之前）
  const row = screen.getByText("最近一次 · 当前产物时效判定用").closest("label");
  expect(row).not.toBeNull();
  expect(row?.textContent).toContain("run-b");
  expect(row?.textContent).not.toContain("run-a");
});

it("产物清理清单不出现时效判定标记", async () => {
  render(
    <CleanupDialog wid="w1" kind="products" onClose={vi.fn()} onCleaned={vi.fn()} />,
  );
  expect(
    await screen.findByRole("checkbox", { name: "s1__lost.txt" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("最近一次 · 当前产物时效判定用")).not.toBeInTheDocument();
});
