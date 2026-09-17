import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import type { components } from "../../api-types.gen";
import { BatchOverview } from "./BatchOverview";
import { itemsFromGroups } from "./items-state";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: {
    scanIntegrity: vi.fn(),
    latestRun: vi.fn(),
    setExclusions: vi.fn(),
    startRun: vi.fn(),
    exportPlan: vi.fn().mockResolvedValue({
      batch: 1,
      included: [],
      excluded: [],
      total_bytes: 0,
      sequential: true,
      non_ascii_names: false,
    }),
  },
}));

const items = itemsFromGroups({
  done: [
    {
      item: "photo",
      name: "photo.jpg",
      status: "done",
      can_retry: true,
      in_retry: true,
      media: "image",
    },
  ],
  failed: [
    {
      item: "clip",
      name: "clip.mp4",
      status: "failed",
      can_retry: false,
      in_retry: false,
      media: "video",
      message: "格式不支持",
    },
  ],
  unimported: [
    {
      item: "new",
      name: "new.jpg",
      status: "unimported",
      can_retry: false,
      in_retry: false,
      media: "image",
    },
  ],
});
const report: components["schemas"]["IntegrityReport"] = {
  checked_at: "2026-09-17T00:00:00Z",
  imports_available: true,
  batches: [
    {
      batch: "s1",
      items: [
        {
          item: "photo",
          name: "photo.jpg",
          status: "changed",
          current_hash: "new",
          labeling_hash: "old",
          detail: "打标后素材已变更。",
        },
      ],
    },
  ],
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.setExclusions).mockResolvedValue({
    id: "s1",
    seq: 1,
    items: ["photo"],
  });
  vi.mocked(api.exportPlan).mockResolvedValue({
    batch: 1,
    included: [],
    excluded: [],
    total_bytes: 0,
    sequential: true,
    non_ascii_names: false,
  });
  vi.mocked(api.scanIntegrity).mockResolvedValue(report);
  vi.mocked(api.latestRun).mockResolvedValue({
    record: null,
    log_path: null,
    items_path: null,
  });
});

describe("批次概览", () => {
  it("变更素材重打必须确认，失败可重试且只提交选中的素材", async () => {
    const user = userEvent.setup();
    const onRunStarted = vi.fn();
    const onImported = vi.fn();
    render(
      <BatchOverview
        wid="work"
        batch="s1"
        items={items}
        onSelect={vi.fn()}
        onRunStarted={onRunStarted}
        onImported={onImported}
      />,
    );
    await user.click(screen.getByRole("button", { name: "校验素材完整性" }));
    const changed = within(
      await screen.findByRole("region", { name: "打标后素材已变更" }),
    );
    await user.click(changed.getByRole("button", { name: "重打" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("photo.jpg")).toBeInTheDocument();
    expect(api.startRun).not.toHaveBeenCalled();
    vi.mocked(api.startRun).mockRejectedValueOnce(new Error("目录已占用"));

    await user.click(dialog.getByRole("button", { name: "重打这 1 条" }));

    expect(await dialog.findByRole("alert")).toHaveTextContent("目录已占用");
    expect(onRunStarted).not.toHaveBeenCalled();
    vi.mocked(api.startRun).mockResolvedValue({ run_id: "selected-run" });
    await user.click(dialog.getByRole("button", { name: "重打这 1 条" }));

    expect(api.startRun).toHaveBeenLastCalledWith("work", "s1", "retry", ["photo"]);
    expect(onRunStarted).toHaveBeenCalledExactlyOnceWith("selected-run");
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("变更清单排除失败保留选择，成功后退出并同步导出清单", async () => {
    const user = userEvent.setup();
    render(<BatchOverview wid="work" batch="s1" items={items} onSelect={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "校验素材完整性" }));
    const changed = within(
      await screen.findByRole("region", { name: "打标后素材已变更" }),
    );
    await user.click(changed.getByRole("button", { name: "选择" }));
    expect(changed.getByRole("button", { name: "排除打包（0）" })).toBeDisabled();
    await user.click(changed.getByRole("button", { name: "全选" }));
    vi.mocked(api.setExclusions).mockRejectedValueOnce(new Error("保存失败"));

    await user.click(changed.getByRole("button", { name: "排除打包（1）" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败");
    expect(
      changed.getByRole("checkbox", { name: "选择变更素材 photo.jpg" }),
    ).toBeChecked();
    vi.mocked(api.exportPlan).mockResolvedValue({
      batch: 1,
      included: [],
      excluded: [
        {
          item: "photo",
          name: "photo.jpg",
          asset_bytes: 12,
          caption_bytes: 6,
          reason: "用户排除",
        },
      ],
      total_bytes: 0,
      sequential: true,
      non_ascii_names: false,
    });
    await user.click(changed.getByRole("button", { name: "排除打包（1）" }));

    expect(await screen.findByText("用户排除")).toBeInTheDocument();
    expect(api.setExclusions).toHaveBeenLastCalledWith("work", "s1", ["photo"], true);
    expect(changed.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(changed.getByRole("button", { name: "选择" })).toBeEnabled();
    expect(api.exportPlan).toHaveBeenCalledTimes(2);
  });

  it("条目增量变化不重算导出计划，完整同步版本变化才重算", async () => {
    const onSelect = vi.fn();
    const { rerender } = render(
      <BatchOverview
        wid="work"
        batch="s1"
        items={items}
        exportRevision={0}
        onSelect={onSelect}
      />,
    );
    await act(async () => {});
    expect(api.exportPlan).toHaveBeenCalledTimes(1);
    const updated = new Map(items);
    const photo = updated.get("photo");
    if (!photo) throw new Error("缺少测试素材");
    updated.set("photo", { ...photo, in_retry: false });

    rerender(
      <BatchOverview
        wid="work"
        batch="s1"
        items={updated}
        exportRevision={0}
        onSelect={onSelect}
      />,
    );
    await act(async () => {});
    expect(api.exportPlan).toHaveBeenCalledTimes(1);
    rerender(
      <BatchOverview
        wid="work"
        batch="s1"
        items={updated}
        exportRevision={1}
        onSelect={onSelect}
      />,
    );
    await act(async () => {});

    expect(api.exportPlan).toHaveBeenCalledTimes(2);
    expect(api.exportPlan).toHaveBeenLastCalledWith("work", "s1", true);
  });

  it("条目汇总不重复计入重试条目且不计未导入，失败条目可直接预览", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<BatchOverview wid="work" batch="s1" items={items} onSelect={onSelect} />);

    expect(
      within(screen.getByRole("region", { name: "条目汇总" })).getByText("总数")
        .nextElementSibling,
    ).toHaveTextContent("2");
    expect(api.scanIntegrity).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /clip\.mp4格式不支持/ }));
    expect(onSelect).toHaveBeenCalledWith(items.get("clip"));
  });

  it("手动校验展示当前批次问题，失败时保留上次报告", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<BatchOverview wid="work" batch="s1" items={items} onSelect={onSelect} />);
    await user.click(screen.getByRole("button", { name: "校验素材完整性" }));

    expect(api.scanIntegrity).toHaveBeenCalledExactlyOnceWith("work", "s1");
    expect(
      await screen.findByRole("region", { name: "打标后素材已变更" }),
    ).toHaveTextContent("photo.jpg");
    await user.click(screen.getByRole("button", { name: "查看" }));
    expect(onSelect).toHaveBeenCalledWith(items.get("photo"));
    vi.mocked(api.scanIntegrity).mockRejectedValue(new Error("磁盘不可读"));
    await user.click(screen.getByRole("button", { name: "校验素材完整性" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("磁盘不可读");
    expect(
      screen.getByRole("region", { name: "打标后素材已变更" }),
    ).toBeInTheDocument();
  });

  it("切换批次后迟到报告不能覆盖新批次", async () => {
    const user = userEvent.setup();
    let finish: ((value: typeof report) => void) | undefined;
    vi.mocked(api.scanIntegrity).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { rerender } = render(
      <BatchOverview wid="work" batch="s1" items={items} onSelect={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: "校验素材完整性" }));
    rerender(<BatchOverview wid="work" batch="s2" items={items} onSelect={vi.fn()} />);
    await act(async () => {
      finish?.(report);
    });

    expect(
      screen.queryByRole("region", { name: "打标后素材已变更" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "校验素材完整性" })).toBeEnabled();
  });

  it("缺少登记记录不能显示校验通过", async () => {
    const user = userEvent.setup();
    vi.mocked(api.scanIntegrity).mockResolvedValue({
      ...report,
      imports_available: false,
      batches: [{ batch: "s1", items: [] }],
    });
    render(<BatchOverview wid="work" batch="s1" items={items} onSelect={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "校验素材完整性" }));

    expect(
      await screen.findByText("缺少导入记录，无法完成缺失对账。"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/校验通过/)).not.toBeInTheDocument();
  });
});
