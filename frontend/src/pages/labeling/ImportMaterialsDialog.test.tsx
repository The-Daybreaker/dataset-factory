import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, type TaskView } from "../../api";
import { ImportMaterialsDialog } from "./ImportMaterialsDialog";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: {
    importMaterials: vi.fn(),
    reimportMaterials: vi.fn(),
    getTask: vi.fn(),
    cancelTask: vi.fn(),
  },
}));

const result = {
  source: "/source",
  imported: ["new.jpg"],
  skipped_identical: [],
  skipped_conflict: [],
  skipped_duplicate: [],
  rejected: [],
};
const completed: TaskView = {
  id: "task",
  status: "succeeded",
  progress: 1,
  result,
  error: null,
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.importMaterials).mockResolvedValue({ task_id: "task" });
  vi.mocked(api.getTask).mockResolvedValue(completed);
});

describe("素材导入", () => {
  it("恢复跳过同容文件时显示实际原因，不把跳过误报为恢复成功", async () => {
    vi.mocked(api.reimportMaterials).mockResolvedValue({ task_id: "task" });
    vi.mocked(api.getTask).mockResolvedValue({
      ...completed,
      result: {
        imports: [
          {
            ...result,
            imported: [],
            skipped_duplicate: [{ name: "new.jpg", duplicate_of: "existing.jpg" }],
          },
        ],
        unavailable: [],
      },
    });
    const user = userEvent.setup();
    render(
      <ImportMaterialsDialog
        wid="work"
        names={["new.jpg"]}
        initialMode="restore"
        onClose={vi.fn()}
        onImported={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "导入" }));

    await screen.findByText(/导入完成 · 新增 0 项/);
    expect(screen.getByText("new.jpg · 与 existing.jpg 相同")).toBeInTheDocument();
    vi.mocked(api.getTask).mockResolvedValue({
      ...completed,
      result: { imports: [result], unavailable: [] },
    });
    await user.click(screen.getByRole("button", { name: "仍按新名导入" }));

    await screen.findByText(/导入完成 · 新增 1 项/);
    expect(api.reimportMaterials).toHaveBeenLastCalledWith(
      "work",
      ["new.jpg"],
      ["new.jpg"],
    );
    expect(api.importMaterials).not.toHaveBeenCalled();
    expect(
      screen.queryByText("new.jpg · 与 existing.jpg 相同"),
    ).not.toBeInTheDocument();
  });
  it("按来源恢复只提交选定文件，汇总恢复结果和来源失效原因", async () => {
    vi.mocked(api.reimportMaterials).mockResolvedValue({ task_id: "task" });
    vi.mocked(api.getTask).mockResolvedValue({
      ...completed,
      result: {
        imports: [result],
        unavailable: [{ name: "lost.jpg", reason: "原始来源不可用" }],
      },
    });
    const user = userEvent.setup();
    const onImported = vi.fn();
    render(
      <ImportMaterialsDialog
        wid="work"
        names={["new.jpg", "lost.jpg"]}
        initialMode="restore"
        onClose={vi.fn()}
        onImported={onImported}
      />,
    );

    await user.click(screen.getByRole("button", { name: "导入" }));

    await screen.findByText(/导入完成 · 新增 1 项/);
    expect(screen.getByText("lost.jpg · 原始来源不可用")).toBeInTheDocument();
    expect(api.reimportMaterials).toHaveBeenCalledExactlyOnceWith("work", [
      "new.jpg",
      "lost.jpg",
    ]);
    expect(api.importMaterials).not.toHaveBeenCalled();
    expect(onImported).toHaveBeenCalledOnce();
  });

  it("从别处恢复只复制指定文件，不导入来源目录的其他文件", async () => {
    const user = userEvent.setup();
    render(
      <ImportMaterialsDialog
        wid="work"
        names={["new.jpg"]}
        initialMode="copy"
        onClose={vi.fn()}
        onImported={vi.fn()}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: "来源目录" }), "/replacement");
    await user.click(screen.getByRole("button", { name: "导入" }));

    await screen.findByText(/导入完成/);
    expect(api.importMaterials).toHaveBeenCalledExactlyOnceWith("work", {
      source: "/replacement",
      names: ["new.jpg"],
    });
  });

  it("成功载荷畸形时保留恢复句柄，重查成功前不报告完成", async () => {
    vi.mocked(api.reimportMaterials).mockResolvedValue({ task_id: "task" });
    vi.mocked(api.getTask)
      .mockResolvedValueOnce({ ...completed, result: {} })
      .mockResolvedValueOnce({
        ...completed,
        result: { imports: [result], unavailable: [] },
      });
    const onImported = vi.fn();
    const user = userEvent.setup();
    render(
      <ImportMaterialsDialog
        wid="work"
        names={["new.jpg"]}
        initialMode="restore"
        onClose={vi.fn()}
        onImported={onImported}
      />,
    );

    await user.click(screen.getByRole("button", { name: "导入" }));
    await screen.findByText("导入结果格式异常");
    expect(onImported).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "重新查询" }));

    await screen.findByText(/导入完成/);
    expect(api.reimportMaterials).toHaveBeenCalledOnce();
    expect(onImported).toHaveBeenCalledOnce();
  });

  it("失败任务重试创建新导入，不再查询失败句柄", async () => {
    vi.mocked(api.importMaterials)
      .mockResolvedValueOnce({ task_id: "failed-task" })
      .mockResolvedValueOnce({ task_id: "retry-task" });
    vi.mocked(api.getTask)
      .mockResolvedValueOnce({
        ...completed,
        id: "failed-task",
        status: "failed",
        error: "来源不可读",
      })
      .mockResolvedValueOnce({ ...completed, id: "retry-task" });
    const user = userEvent.setup();
    render(<ImportMaterialsDialog wid="work" onClose={vi.fn()} onImported={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "导入" }));
    await screen.findByText("来源不可读");
    await user.click(screen.getByRole("button", { name: "导入" }));

    await screen.findByText(/导入完成/);
    expect(api.importMaterials).toHaveBeenCalledTimes(2);
    expect(api.getTask).toHaveBeenNthCalledWith(2, "retry-task");
  });

  it("任务丢失后回到原入口，允许重新提交", async () => {
    vi.mocked(api.getTask).mockRejectedValueOnce(
      new ApiError("http", "任务不存在", 404, null),
    );
    const user = userEvent.setup();
    render(<ImportMaterialsDialog wid="work" onClose={vi.fn()} onImported={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "导入" }));
    await screen.findByText("上次任务已丢失（服务重启过），可重新执行");
    expect(screen.queryByRole("button", { name: "重新查询" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "导入" }));

    await screen.findByText(/导入完成/);
    expect(api.importMaterials).toHaveBeenCalledTimes(2);
  });

  it("逐条强制导入后保留其余重复文件供继续处理", async () => {
    vi.mocked(api.getTask)
      .mockResolvedValueOnce({
        ...completed,
        result: {
          ...result,
          skipped_duplicate: [
            { name: "copy-a.jpg", duplicate_of: "original.jpg" },
            { name: "copy-b.jpg", duplicate_of: "original.jpg" },
          ],
        },
      })
      .mockResolvedValueOnce({
        ...completed,
        result: { ...result, imported: ["copy-a.jpg"] },
      });
    const user = userEvent.setup();
    render(<ImportMaterialsDialog wid="work" onClose={vi.fn()} onImported={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "导入" }));
    const buttons = await screen.findAllByRole("button", { name: "仍按新名导入" });
    const first = buttons[0];
    if (!first) throw new Error("缺少逐条导入按钮");
    await user.click(first);

    await screen.findByText(/导入完成 · 新增 2 项/);
    expect(screen.getByText("copy-b.jpg · 与 original.jpg 相同")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "仍按新名导入" })).toHaveLength(1);
  });

  it("受理不算完成，真实任务成功后才刷新条目", async () => {
    let resolveTask: ((task: TaskView) => void) | undefined;
    vi.mocked(api.getTask).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveTask = resolve;
        }),
    );
    const onImported = vi.fn();
    const user = userEvent.setup();
    render(
      <ImportMaterialsDialog wid="work" onClose={vi.fn()} onImported={onImported} />,
    );

    await user.click(screen.getByRole("button", { name: "导入" }));

    expect(api.importMaterials).toHaveBeenCalledWith("work", { source: null });
    expect(onImported).not.toHaveBeenCalled();
    expect(screen.getByRole("progressbar", { name: "导入进度" })).toBeInTheDocument();
    await act(async () => {
      resolveTask?.(completed);
    });
    expect(onImported).toHaveBeenCalledOnce();
    expect(screen.getByText(/导入完成 · 新增 1 项/)).toBeInTheDocument();
  });

  it("异名同容逐条补导沿用报告来源并只提交所选文件", async () => {
    vi.mocked(api.getTask).mockResolvedValueOnce({
      ...completed,
      result: {
        ...result,
        skipped_duplicate: [{ name: "copy.jpg", duplicate_of: "original.jpg" }],
      },
    });
    const user = userEvent.setup();
    render(<ImportMaterialsDialog wid="work" onClose={vi.fn()} onImported={vi.fn()} />);
    await user.click(screen.getByRole("radio", { name: "复制导入" }));
    await user.type(
      screen.getByRole("textbox", { name: "来源目录" }),
      "/original-source",
    );
    await user.click(screen.getByRole("button", { name: "导入" }));
    await screen.findByRole("button", { name: "仍按新名导入" });
    await user.clear(screen.getByRole("textbox", { name: "来源目录" }));
    await user.type(screen.getByRole("textbox", { name: "来源目录" }), "/changed");

    await user.click(screen.getByRole("button", { name: "仍按新名导入" }));

    expect(api.importMaterials).toHaveBeenLastCalledWith("work", {
      source: "/original-source",
      names: ["copy.jpg"],
      force_names: ["copy.jpg"],
    });
  });

  it("查询失败可按原句柄重查，不重复导入", async () => {
    vi.mocked(api.getTask).mockRejectedValueOnce(new Error("网络中断"));
    const user = userEvent.setup();
    render(<ImportMaterialsDialog wid="work" onClose={vi.fn()} onImported={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "导入" }));
    await screen.findByText("网络中断");

    await user.click(screen.getByRole("button", { name: "重新查询" }));

    await waitFor(() => expect(screen.getByText(/导入完成/)).toBeInTheDocument());
    expect(api.importMaterials).toHaveBeenCalledOnce();
    expect(api.getTask).toHaveBeenNthCalledWith(2, "task");
  });
});
