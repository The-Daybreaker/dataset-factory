import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../../api";
import type { components } from "../../api-types.gen";
import { CaptionPreview } from "./CaptionPreview";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return {
    ...actual,
    api: { readCaption: vi.fn(), addRetryItems: vi.fn(), removeRetryItem: vi.fn() },
  };
});

const row: components["schemas"]["ItemRowView"] = {
  item: "frame",
  name: "frame.jpg",
  status: "done",
  media: "image",
  can_retry: true,
  in_retry: false,
};

const batches: components["schemas"]["BatchView"][] = [1, 2, 3, 4, 5].map((seq) => ({
  id: `s${seq}`,
  seq,
  name: `策略${seq}`,
  active: seq !== 5,
  created_at: "2026-09-17T00:00:00Z",
  description: "",
  product_count: 1,
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.readCaption).mockResolvedValue("A detailed caption.");
});

describe("产物与重试预览", () => {
  it("当前策略固定第一栏且最多同时对比三套启用策略", async () => {
    const user = userEvent.setup();
    render(
      <CaptionPreview
        wid="work"
        batch="s1"
        row={row}
        batches={batches}
        onRetryChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "策略1 · s1" })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "策略5 · s5" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "策略3 · s3" }));
    await user.click(screen.getByRole("button", { name: "策略2 · s2" }));

    expect(
      screen
        .getAllByRole("region")
        .filter((entry) => /^产物 s/.test(entry.getAttribute("aria-label") ?? ""))
        .map((entry) => entry.getAttribute("aria-label")),
    ).toEqual(["产物 s1", "产物 s3", "产物 s2"]);
    expect(screen.getByRole("button", { name: "策略4 · s4" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "策略3 · s3" }));
    expect(screen.queryByRole("region", { name: "产物 s3" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "策略4 · s4" })).toBeEnabled();
  });

  it("各对比栏读取独立批次并分别展示无产物和读取错误", async () => {
    const user = userEvent.setup();
    vi.mocked(api.readCaption).mockImplementation(async (_wid, batch) => {
      if (batch === "s2") throw new ApiError("http", "没有产物", 404, null);
      if (batch === "s3") throw new Error("读取失败");
      return "Current caption";
    });
    render(
      <CaptionPreview
        wid="work"
        batch="s1"
        row={row}
        batches={batches}
        onRetryChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "策略2 · s2" }));
    await user.click(screen.getByRole("button", { name: "策略3 · s3" }));

    expect(
      within(screen.getByRole("region", { name: "产物 s1" })).getByText(
        "Current caption",
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "产物 s2" })).getByText("暂无产物"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "产物 s3" })).getByRole("alert"),
    ).toHaveTextContent("读取失败");
    expect(api.readCaption).toHaveBeenCalledWith("work", "s2", "frame");
    expect(api.readCaption).toHaveBeenCalledWith("work", "s3", "frame");
  });

  it("同一素材状态更新后重新读取产物并保留对比选择", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <CaptionPreview
        wid="work"
        batch="s1"
        row={row}
        batches={batches}
        onRetryChange={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "策略2 · s2" }));
    vi.mocked(api.readCaption).mockResolvedValue("Updated caption");

    rerender(
      <CaptionPreview
        wid="work"
        batch="s1"
        row={{ ...row }}
        batches={batches}
        onRetryChange={vi.fn()}
      />,
    );

    expect(await screen.findByText("Updated caption")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "产物 s2" })).toBeInTheDocument();
  });
  it("加入名单成功后只回传服务端确认的名单", async () => {
    const user = userEvent.setup();
    const onRetryChange = vi.fn();
    vi.mocked(api.addRetryItems).mockResolvedValue({
      id: "s1",
      seq: 1,
      items: ["frame"],
    });
    render(
      <CaptionPreview wid="work" batch="s1" row={row} onRetryChange={onRetryChange} />,
    );

    await screen.findByText("A detailed caption.");
    await user.click(screen.getByRole("button", { name: "加入重试列表" }));

    expect(api.addRetryItems).toHaveBeenCalledExactlyOnceWith("work", "s1", ["frame"]);
    expect(onRetryChange).toHaveBeenCalledExactlyOnceWith(["frame"]);
  });

  it("名单保存失败保留原状并展示原因", async () => {
    const user = userEvent.setup();
    const onRetryChange = vi.fn();
    vi.mocked(api.addRetryItems).mockRejectedValue(new Error("该条目不可重试"));
    render(
      <CaptionPreview wid="work" batch="s1" row={row} onRetryChange={onRetryChange} />,
    );

    await user.click(screen.getByRole("button", { name: "加入重试列表" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("该条目不可重试");
    expect(onRetryChange).not.toHaveBeenCalled();
  });

  it("名单中缺失的素材仍可移出", async () => {
    const user = userEvent.setup();
    const onRetryChange = vi.fn();
    vi.mocked(api.removeRetryItem).mockResolvedValue({ id: "s1", seq: 1, items: [] });
    render(
      <CaptionPreview
        wid="work"
        batch="s1"
        row={{ ...row, status: "missing", can_retry: false, in_retry: true }}
        onRetryChange={onRetryChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "移出重试列表" }));

    expect(api.removeRetryItem).toHaveBeenCalledExactlyOnceWith("work", "s1", "frame");
    expect(onRetryChange).toHaveBeenCalledExactlyOnceWith([]);
  });

  it("不可重试条目不提供可执行的加入按钮", async () => {
    vi.mocked(api.readCaption).mockRejectedValue(
      new ApiError("http", "尚无产物", 404, null),
    );
    render(
      <CaptionPreview
        wid="work"
        batch="s1"
        row={{ ...row, can_retry: false }}
        onRetryChange={vi.fn()}
      />,
    );

    expect(await screen.findByText("暂无产物")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "加入重试列表" })).toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("切换素材后迟到的旧 caption 不覆盖新素材", async () => {
    let finish: ((caption: string) => void) | undefined;
    vi.mocked(api.readCaption).mockImplementation((_wid, _batch, item) =>
      item === "frame"
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : Promise.resolve("New caption"),
    );
    const { rerender } = render(
      <CaptionPreview wid="work" batch="s1" row={row} onRetryChange={vi.fn()} />,
    );

    rerender(
      <CaptionPreview
        wid="work"
        batch="s1"
        row={{ ...row, item: "new" }}
        onRetryChange={vi.fn()}
      />,
    );
    await screen.findByText("New caption");
    await act(async () => {
      finish?.("Old caption");
    });

    expect(screen.queryByText("Old caption")).not.toBeInTheDocument();
    expect(screen.getByText("New caption")).toBeInTheDocument();
  });
});
