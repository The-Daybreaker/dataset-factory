import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { toast } from "../../lib/toast";
import { ToastViewport } from "./toast";

/**
 * 浮层提示的渲染与消失（形态见 §5.11：黑底白字、居中贴底、几秒后自己走）。
 *
 * 计时器全程造假：驻留时长 3.5 秒，用真时间等就等于每个用例白等 4 秒。
 */
describe("ToastViewport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.advanceTimersByTime(5_000);
    vi.useRealTimers();
  });

  it("投递后进入状态区域，超过驻留时长自动消失", () => {
    render(<ToastViewport />);
    act(() => toast("服务可能已关闭"));

    expect(screen.getByText("服务可能已关闭")).toBeVisible();

    act(() => vi.advanceTimersByTime(4_000));
    expect(screen.queryByText("服务可能已关闭")).not.toBeInTheDocument();
  });

  it("不等自动消失也可以点掉，且只点掉自己那一条", () => {
    render(<ToastViewport />);
    act(() => {
      toast("服务可能已关闭");
      toast("另一条");
    });

    fireEvent.click(screen.getByText("服务可能已关闭"));

    expect(screen.queryByText("服务可能已关闭")).not.toBeInTheDocument();
    expect(screen.getByText("另一条")).toBeInTheDocument();
  });
});
