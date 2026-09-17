import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type TaskView } from "../../api";
import { RebuildImportsDialog } from "./RebuildImportsDialog";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: { rebuildImportRecords: vi.fn(), getTask: vi.fn() },
}));

const succeeded: TaskView = {
  id: "rebuild-1",
  status: "succeeded",
  progress: 1,
  result: { file_count: 3, source: "" },
  error: null,
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.rebuildImportRecords).mockResolvedValue({ task_id: "rebuild-1" });
  vi.mocked(api.getTask).mockResolvedValue(succeeded);
});

describe("重建导入记录", () => {
  it("确认前不提交，只有成功终态才通知刷新并显示登记数", async () => {
    const user = userEvent.setup();
    const onRebuilt = vi.fn();
    render(<RebuildImportsDialog wid="work" onClose={vi.fn()} onRebuilt={onRebuilt} />);
    expect(api.rebuildImportRecords).not.toHaveBeenCalled();
    expect(screen.getByText(/产物时效仍无法校验/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重建导入记录" }));
    expect(await screen.findByText("重建完成 · 登记素材 3 条")).toBeInTheDocument();
    expect(api.rebuildImportRecords).toHaveBeenCalledExactlyOnceWith("work");
    expect(onRebuilt).toHaveBeenCalledTimes(1);
  });

  it("查询失败保留原句柄，重新查询不再次提交", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getTask).mockRejectedValueOnce(new Error("连接中断"));
    const onRebuilt = vi.fn();
    render(<RebuildImportsDialog wid="work" onClose={vi.fn()} onRebuilt={onRebuilt} />);
    await user.click(screen.getByRole("button", { name: "重建导入记录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("连接中断");
    expect(onRebuilt).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "重建导入记录" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "重新查询" }));
    expect(await screen.findByText("重建完成 · 登记素材 3 条")).toBeInTheDocument();
    expect(api.rebuildImportRecords).toHaveBeenCalledTimes(1);
    expect(api.getTask).toHaveBeenNthCalledWith(2, "rebuild-1");
  });

  it("失败终态展示原因而不宣称重建成功", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getTask).mockResolvedValue({
      ...succeeded,
      status: "failed",
      result: null,
      error: "素材主干冲突",
    });
    const onRebuilt = vi.fn();
    render(<RebuildImportsDialog wid="work" onClose={vi.fn()} onRebuilt={onRebuilt} />);
    await user.click(screen.getByRole("button", { name: "重建导入记录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("素材主干冲突");
    expect(onRebuilt).not.toHaveBeenCalled();
    expect(screen.queryByText(/重建完成/)).not.toBeInTheDocument();
  });

  it("卸载后迟到的成功响应不触发旧目录刷新", async () => {
    const user = userEvent.setup();
    let finish: ((value: TaskView) => void) | undefined;
    vi.mocked(api.getTask).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const onRebuilt = vi.fn();
    const { unmount } = render(
      <RebuildImportsDialog wid="work" onClose={vi.fn()} onRebuilt={onRebuilt} />,
    );
    await user.click(screen.getByRole("button", { name: "重建导入记录" }));
    unmount();
    await act(async () => {
      finish?.(succeeded);
    });
    expect(onRebuilt).not.toHaveBeenCalled();
  });
});
