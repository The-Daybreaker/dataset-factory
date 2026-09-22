import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, type TaskView } from "../../api";
import type { components } from "../../api-types.gen";
import { TooltipProvider } from "../../components/ui/tooltip";
import { NewBatchForm } from "./NewBatchForm";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: {
    listStrategies: vi.fn(),
    listEndpoints: vi.fn(),
    listPrompts: vi.fn(),
    listSkills: vi.fn(),
    createWorkdir: vi.fn(),
    importMaterials: vi.fn(),
    getTask: vi.fn(),
    createBatch: vi.fn(),
    listItems: vi.fn(),
    startRun: vi.fn(),
    scanPreview: vi.fn(),
  },
}));

const completed: TaskView = {
  id: "initial",
  status: "succeeded",
  progress: 1,
  error: null,
  result: {
    source: "/source",
    imported: ["image.jpg"],
    skipped_identical: [],
    skipped_conflict: [],
    skipped_duplicate: [],
    rejected: [],
  },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.scanPreview).mockResolvedValue({
    total: 1,
    images: 1,
    videos: 0,
    unimported: [],
  });
  vi.mocked(api.listStrategies).mockResolvedValue([]);
  vi.mocked(api.listSkills).mockResolvedValue([]);
  vi.mocked(api.listPrompts).mockResolvedValue([
    { id: "p-caption-01", name: "caption", description: "" },
  ]);
  vi.mocked(api.listEndpoints).mockResolvedValue([
    {
      id: "e-model-x1111",
      name: "model",
      model: "test",
      is_active: true,
      has_api_key: true,
      base_url: "https://example.invalid",
      api_format: "openai-chat-completions",
      request_params: {},
    },
  ]);
  vi.mocked(api.createWorkdir).mockResolvedValue({
    task_id: "initial",
    workdir: {
      id: "work",
      title: "work",
      path: "/work",
      last_used_at: 0,
    },
  });
  vi.mocked(api.importMaterials).mockResolvedValue({ task_id: "retry" });
  vi.mocked(api.getTask).mockResolvedValue(completed);
  vi.mocked(api.createBatch).mockResolvedValue({
    id: "s1",
    seq: 1,
    name: "Test",
    description: "",
    active: true,
    created_at: "",
    product_count: 0,
  });
  vi.mocked(api.listItems).mockResolvedValue({ batch: 1, query: "", groups: {} });
  vi.mocked(api.startRun).mockResolvedValue({ run_id: "run" });
});

async function fillForm() {
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox", { name: "来源目录" }), "/source");
  await user.type(screen.getByRole("textbox", { name: "工作目录" }), "/work");
  await user.type(screen.getByRole("textbox", { name: "策略名" }), "Test");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "开始打标" })).toBeEnabled(),
  );
  return user;
}

describe("新建跑批恢复", () => {
  it("首次导入展示重复报告并可逐条按新名导入，确认后复用工作目录启动", async () => {
    vi.mocked(api.getTask).mockResolvedValue({
      ...completed,
      result: {
        ...(completed.result as Record<string, unknown>),
        skipped_identical: ["same.jpg"],
        skipped_conflict: [
          {
            name: "changed.jpg",
            existing_size: 1,
            incoming_size: 2,
            existing_sha256: "a".repeat(64),
            incoming_sha256: "b".repeat(64),
          },
        ],
        skipped_duplicate: [{ name: "copy.jpg", duplicate_of: "image.jpg" }],
      },
    });
    const onCreated = vi.fn();
    render(
      <TooltipProvider>
        <NewBatchForm onBack={vi.fn()} onCreated={onCreated} />
      </TooltipProvider>,
    );
    const user = await fillForm();

    await user.click(screen.getByRole("button", { name: "开始打标" }));
    const dialog = within(await screen.findByRole("dialog", { name: "导入素材" }));
    expect(dialog.getByText(/同名同容跳过 1 项/)).toBeInTheDocument();
    expect(
      dialog.getByText("1 个同名文件内容不同，已跳过、未覆盖"),
    ).toBeInTheDocument();
    expect(api.startRun).not.toHaveBeenCalled();
    expect(api.createBatch).not.toHaveBeenCalled();
    vi.mocked(api.getTask).mockResolvedValue({ ...completed, id: "retry" });
    await user.click(dialog.getByRole("button", { name: "仍按新名导入" }));
    await waitFor(() =>
      expect(
        dialog.queryByRole("button", { name: "仍按新名导入" }),
      ).not.toBeInTheDocument(),
    );
    expect(api.importMaterials).toHaveBeenCalledExactlyOnceWith("work", {
      source: "/source",
      names: ["copy.jpg"],
      force_names: ["copy.jpg"],
    });
    const close = dialog.getAllByRole("button", { name: "关闭" })[0];
    if (!close) throw new Error("缺少关闭按钮");
    await user.click(close);
    await user.click(screen.getByRole("button", { name: "开始打标" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(api.createWorkdir).toHaveBeenCalledOnce();
    expect(api.createBatch).toHaveBeenCalledOnce();
  });

  it("预检转去导入保留复制模式与来源，并在补登记后重新预检才启动", async () => {
    vi.mocked(api.listItems).mockResolvedValueOnce({
      batch: 1,
      query: "",
      groups: {
        unimported: [
          {
            item: "extra",
            name: "extra.png",
            status: "unimported",
            media: "image",
            in_retry: false,
            can_retry: false,
            reason: "未登记",
          },
        ],
      },
    });
    const onCreated = vi.fn();
    render(<NewBatchForm onBack={vi.fn()} onCreated={onCreated} />);
    const user = await fillForm();
    await user.click(screen.getByRole("button", { name: "开始打标" }));
    await user.click(await screen.findByRole("button", { name: "先去导入" }));
    const dialog = within(await screen.findByRole("dialog", { name: "导入素材" }));
    expect(dialog.getByRole("radio", { name: "复制导入" })).toBeChecked();
    expect(dialog.getByRole("textbox", { name: "来源目录" })).toHaveValue("/source");
    vi.mocked(api.getTask).mockResolvedValue({ ...completed, id: "retry" });

    await user.click(dialog.getByRole("button", { name: "导入" }));

    await dialog.findByText(/导入完成 · 新增 1 项/);
    expect(api.importMaterials).toHaveBeenCalledExactlyOnceWith("work", {
      source: "/source",
    });
    expect(api.startRun).not.toHaveBeenCalled();
    const close = dialog.getAllByRole("button", { name: "关闭" })[0];
    if (!close) throw new Error("缺少关闭按钮");
    await user.click(close);
    await user.click(screen.getByRole("button", { name: "开始打标" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(api.createWorkdir).toHaveBeenCalledOnce();
    expect(api.createBatch).toHaveBeenCalledOnce();
    expect(api.listItems).toHaveBeenCalledTimes(2);
  });

  it("预检按文件名去重并在再次预检时排除已补登记的素材", async () => {
    vi.mocked(api.getTask).mockResolvedValue({
      ...completed,
      result: {
        source: "/source",
        imported: [],
        skipped_identical: [],
        skipped_conflict: [],
        skipped_duplicate: [],
        rejected: [{ name: "large.mov", reason: "超出大小上限" }],
      },
    });
    const row: components["schemas"]["ItemRowView"] = {
      item: "large",
      name: "large.mov",
      status: "unimported",
      media: "video",
      in_retry: false,
      can_retry: false,
      reason: "超出大小上限",
    };
    vi.mocked(api.listItems).mockResolvedValueOnce({
      batch: 1,
      query: "",
      groups: { unimported: [row] },
    });
    const onCreated = vi.fn();
    render(<NewBatchForm onBack={vi.fn()} onCreated={onCreated} />);
    const user = await fillForm();

    await user.click(screen.getByRole("button", { name: "开始打标" }));

    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getAllByText("large.mov")).toHaveLength(1);
    expect(api.startRun).not.toHaveBeenCalled();
    await user.click(dialog.getByRole("button", { name: "关闭" }));
    vi.mocked(api.listItems).mockResolvedValue({
      batch: 1,
      query: "",
      groups: { queued: [{ ...row, status: "queued", reason: null }] },
    });
    await user.click(screen.getByRole("button", { name: "开始打标" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(api.startRun).toHaveBeenCalledExactlyOnceWith("work", "s1", "full");
    expect(api.createWorkdir).toHaveBeenCalledOnce();
    expect(api.createBatch).toHaveBeenCalledOnce();
  });

  it("来源中被拒绝的文件即使未复制到工作目录也在弹窗确认后才启动", async () => {
    vi.mocked(api.getTask).mockResolvedValue({
      ...completed,
      result: {
        source: "/source",
        imported: ["image.jpg"],
        skipped_identical: [],
        skipped_conflict: [],
        skipped_duplicate: [],
        rejected: [
          { name: "design.psd", reason: "扩展名不支持" },
          { name: "large.mov", reason: "超出大小上限" },
        ],
      },
    });
    const onCreated = vi.fn();
    render(<NewBatchForm onBack={vi.fn()} onCreated={onCreated} />);
    const user = await fillForm();

    await user.click(screen.getByRole("button", { name: "开始打标" }));

    const dialog = await screen.findByRole("dialog", { name: "开始打标前确认" });
    expect(dialog).toHaveTextContent("有 2 个素材未登记");
    expect(dialog).toHaveTextContent("design.psd");
    expect(dialog).toHaveTextContent("扩展名不支持");
    expect(dialog).toHaveTextContent("large.mov");
    expect(api.startRun).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "仍要开始" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(api.startRun).toHaveBeenCalledExactlyOnceWith("work", "s1", "full");
    expect(api.createWorkdir).toHaveBeenCalledOnce();
    expect(api.createBatch).toHaveBeenCalledOnce();
  });

  it.each(["failed", "cancelled", "lost"])(
    "初始导入 %s 后重新导入，不重复登记或提前建批",
    async (status) => {
      if (status === "lost")
        vi.mocked(api.getTask).mockRejectedValueOnce(
          new ApiError("http", "lost", 404, null),
        );
      else
        vi.mocked(api.getTask).mockResolvedValueOnce({
          ...completed,
          status: status === "failed" ? "failed" : "cancelled",
          error: "导入中断",
        });
      vi.mocked(api.getTask).mockResolvedValue({ ...completed, id: "retry" });
      const onCreated = vi.fn();
      render(<NewBatchForm onBack={vi.fn()} onCreated={onCreated} />);
      const user = await fillForm();

      await user.click(screen.getByRole("button", { name: "开始打标" }));
      await screen.findByRole("alert");
      expect(api.createBatch).not.toHaveBeenCalled();
      await user.click(screen.getByRole("button", { name: "开始打标" }));

      await waitFor(() =>
        expect(onCreated).toHaveBeenCalledWith({ workdirId: "work", batchId: "s1" }),
      );
      expect(api.createWorkdir).toHaveBeenCalledOnce();
      expect(api.importMaterials).toHaveBeenCalledExactlyOnceWith("work", {
        source: "/source",
      });
      expect(api.getTask).toHaveBeenNthCalledWith(2, "retry");
      expect(api.createBatch).toHaveBeenCalledOnce();
    },
  );

  it("临时查询失败继续查询原任务，不再发送导入", async () => {
    vi.mocked(api.getTask).mockRejectedValueOnce(new Error("离线"));
    const onCreated = vi.fn();
    render(<NewBatchForm onBack={vi.fn()} onCreated={onCreated} />);
    const user = await fillForm();

    await user.click(screen.getByRole("button", { name: "开始打标" }));
    await screen.findByText("离线");
    await user.click(screen.getByRole("button", { name: "开始打标" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(api.importMaterials).not.toHaveBeenCalled();
    expect(api.getTask).toHaveBeenNthCalledWith(2, "initial");
  });

  it("发现未导入素材时保留建批结果，明确确认后才发车", async () => {
    vi.mocked(api.listItems).mockResolvedValue({
      batch: 1,
      query: "",
      groups: {
        unimported: [
          {
            item: "extra",
            name: "extra.jpg",
            status: "unimported",
            media: "image",
            in_retry: false,
            can_retry: false,
            reason: "未登记",
          },
        ],
      },
    });
    const onCreated = vi.fn();
    render(<NewBatchForm onBack={vi.fn()} onCreated={onCreated} />);
    const user = await fillForm();

    await user.click(screen.getByRole("button", { name: "开始打标" }));
    await screen.findByRole("button", { name: "仍要开始" });
    expect(api.startRun).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "仍要开始" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(api.createBatch).toHaveBeenCalledOnce();
    expect(api.createWorkdir).toHaveBeenCalledOnce();
    expect(api.startRun).toHaveBeenCalledExactlyOnceWith("work", "s1", "full");
  });
});
