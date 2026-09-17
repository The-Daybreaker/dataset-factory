import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../../api";
import { RunControl } from "./RunControl";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: { currentRun: vi.fn(), startRun: vi.fn(), stopRun: vi.fn(), listItems: vi.fn() },
}));

class FakeEventSource {
  static last: FakeEventSource | undefined;
  listeners = new Map<string, (event: MessageEvent) => void>();
  onerror: (() => void) | null = null;
  onopen: (() => void | Promise<void>) | null = null;
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.set(type, listener);
  }
  close(): void {}
  emit(type: string, data: unknown): void {
    this.listeners.get(type)?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}
vi.stubGlobal("EventSource", FakeEventSource);

afterEach(() => {
  vi.useRealTimers();
});

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.currentRun)
    .mockRejectedValueOnce(new ApiError("http", "none", 404, null))
    .mockResolvedValue({
      run_id: "run",
      status: "running",
      batch: 1,
      mode: "full",
      counters: {},
      current_item: null,
      error: null,
    });
  vi.mocked(api.startRun).mockResolvedValue({ run_id: "run" });
  vi.mocked(api.listItems).mockResolvedValue({ batch: 1, query: "", groups: {} });
});

describe("运行控制", () => {
  it("待启动不算结束，失败帧保留原因", async () => {
    vi.mocked(api.currentRun).mockReset().mockResolvedValue({
      run_id: "run",
      status: "pending",
      batch: 1,
      mode: "full",
      counters: {},
      current_item: null,
      error: null,
    });
    const onFinish = vi.fn();
    render(<RunControl wid="work" batch="s1" onFinish={onFinish} />);
    expect(await screen.findByRole("button", { name: "停止" })).toBeInTheDocument();
    expect(onFinish).not.toHaveBeenCalled();

    act(() =>
      FakeEventSource.last?.emit("run-finished", {
        run_id: "run",
        batch: 1,
        status: "failed",
        error: "工作目录正在维护",
      }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent("工作目录正在维护");
    expect(onFinish).toHaveBeenCalledOnce();
  });

  it("空闲页面发现外部启动的运行且不会反复通知结束", async () => {
    vi.useFakeTimers();
    const onFinish = vi.fn();
    render(<RunControl wid="work" batch="s1" onFinish={onFinish} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(screen.getByRole("button", { name: "停止" })).toBeInTheDocument();
    expect(onFinish).not.toHaveBeenCalled();
    act(() =>
      FakeEventSource.last?.emit("run-finished", {
        run_id: "run",
        batch: 1,
        status: "completed",
      }),
    );
    vi.mocked(api.currentRun).mockRejectedValue(
      new ApiError("http", "none", 404, null),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(onFinish).toHaveBeenCalledOnce();
  });

  it("外部运行没有 SSE 时以 current 轮询刷新素材", async () => {
    vi.useFakeTimers();
    vi.mocked(api.currentRun).mockReset().mockResolvedValue({
      run_id: "cli",
      status: "running",
      batch: 1,
      mode: "full",
      counters: {},
      current_item: "image",
      error: null,
    });
    const onFinish = vi.fn();
    render(<RunControl wid="work" batch="s1" onFinish={onFinish} />);
    await act(async () => {});

    act(() => FakeEventSource.last?.onerror?.());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(onFinish).toHaveBeenCalledOnce();
    expect(screen.getByText("正在处理：image")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "停止" })).toBeEnabled();
  });

  it("重连全量刷新未完成时暂存增量，刷新后再应用", async () => {
    const user = userEvent.setup();
    let resolveRefresh: (() => void) | undefined;
    const onFinish = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveRefresh = resolve;
        }),
    );
    const onItemUpdate = vi.fn();
    render(
      <RunControl
        wid="work"
        batch="s1"
        onFinish={onFinish}
        onItemUpdate={onItemUpdate}
      />,
    );
    await user.click(screen.getByRole("button", { name: "开始打标" }));
    const update = {
      batch: 1,
      item: "frame",
      status: "succeeded",
      attempt: 1,
      reason_code: null,
      message: null,
    };

    act(() => {
      void FakeEventSource.last?.onopen?.();
    });
    act(() => {
      FakeEventSource.last?.emit("item-updated", update);
    });

    expect(onItemUpdate).not.toHaveBeenCalled();
    await act(async () => {
      resolveRefresh?.();
    });
    expect(onItemUpdate).toHaveBeenCalledExactlyOnceWith(update);
  });

  it("断线后旧连接的结束事件不能结束重连中的运行", async () => {
    const user = userEvent.setup();
    const onFinish = vi.fn();
    render(<RunControl wid="work" batch="s1" onFinish={onFinish} />);
    await user.click(screen.getByRole("button", { name: "开始打标" }));
    const oldSource = FakeEventSource.last;
    expect(oldSource).toBeDefined();
    vi.useFakeTimers();

    act(() => {
      oldSource?.onerror?.();
      oldSource?.emit("run-finished", { run_id: "run", batch: 1, status: "completed" });
    });

    expect(screen.getByRole("button", { name: "停止" })).toBeInTheDocument();
    expect(onFinish).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const newSource = FakeEventSource.last;
    expect(newSource).not.toBe(oldSource);
    expect(onFinish).toHaveBeenCalledOnce();
    onFinish.mockClear();
    act(() => {
      oldSource?.emit("run-finished", { run_id: "run", batch: 1, status: "completed" });
      newSource?.emit("run-finished", {
        run_id: "other-run",
        batch: 1,
        status: "completed",
      });
    });
    expect(onFinish).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "停止" })).toBeInTheDocument();
    act(() => {
      newSource?.emit("run-finished", { run_id: "run", batch: 1, status: "completed" });
    });
    expect(onFinish).toHaveBeenCalledExactlyOnceWith();
    expect(screen.getByRole("button", { name: "开始打标" })).toBeEnabled();
  });

  it("未导入素材先确认，确认后仍可启动全量跑批", async () => {
    const user = userEvent.setup();
    const onFinish = vi.fn();
    vi.mocked(api.listItems).mockResolvedValue({
      batch: 1,
      query: "",
      groups: {
        unimported: [
          {
            item: "bad",
            name: "bad.heic",
            reason: "扩展名不支持",
            status: "unimported",
            media: "file",
            can_retry: false,
            in_retry: false,
          },
        ],
      },
    });
    render(<RunControl wid="work" batch="s1" onFinish={onFinish} />);

    await user.click(screen.getByRole("button", { name: "开始打标" }));
    expect(
      screen.getByText("有 1 个工作目录文件未登记，不会进入本次跑批。"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "仍要开始" }));

    expect(api.startRun).toHaveBeenCalledWith("work", "s1", "full");
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  it("事件更新当前素材，结束后触发全量刷新", async () => {
    const user = userEvent.setup();
    const onFinish = vi.fn();
    render(<RunControl wid="work" batch="s1" onFinish={onFinish} />);

    await user.click(screen.getByRole("button", { name: "开始打标" }));
    act(() => {
      FakeEventSource.last?.emit("item-updated", {
        batch: 1,
        item: "cat_002",
        status: "started",
        attempt: 1,
        reason_code: null,
        message: null,
      });
    });

    expect(screen.getByText("正在处理：cat_002")).toBeInTheDocument();
    act(() => {
      FakeEventSource.last?.emit("run-finished", {
        run_id: "run",
        batch: 1,
        status: "completed",
      });
    });

    expect(onFinish).toHaveBeenCalledExactlyOnceWith();
    expect(screen.getByRole("button", { name: "开始打标" })).toBeEnabled();
  });

  it("重试模式直接启动，不再弹未导入确认", async () => {
    const user = userEvent.setup();
    render(<RunControl wid="work" batch="s1" onFinish={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "开始重试" }));

    expect(api.startRun).toHaveBeenCalledWith("work", "s1", "retry");
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  it("启动失败显示错误且不进入运行态", async () => {
    const user = userEvent.setup();
    vi.mocked(api.startRun).mockRejectedValue(new Error("已有跑批在运行"));
    render(<RunControl wid="work" batch="s1" onFinish={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "开始打标" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("已有跑批在运行");
    expect(screen.getByRole("button", { name: "开始打标" })).toBeEnabled();
  });
});
