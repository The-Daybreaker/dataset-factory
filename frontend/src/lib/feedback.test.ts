import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import { reportError } from "./feedback";
import { getToastSnapshot } from "./toast";

/**
 * 错误呈现分流的判据（连接类走浮层、业务错误就地展示）。
 *
 * 每个用例结束都把浮层计时器放完，让清单回到空——浮层清单是模块级状态，跨用例不清就会
 * 串味（测试跑在同一个 worker 里，`isolate: false`）。
 */
describe("错误呈现分流", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.advanceTimersByTime(5_000);
    vi.useRealTimers();
  });

  it("连不上后端：只给一句浮层提示，界面不留占位文字", () => {
    expect(
      reportError(new ApiError("network", "无法连接后端服务", null, null)),
    ).toBeNull();
    expect(getToastSnapshot().map((item) => item.text)).toEqual(["无法连接后端服务"]);
  });

  it("连不上时广播一次状态变化，让侧栏状态点当场重查", () => {
    const changed = vi.fn();
    window.addEventListener("df:service-changed", changed);
    reportError(new ApiError("timeout", "等待 30 秒没有响应", null, null));
    window.removeEventListener("df:service-changed", changed);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("后端返回的业务错误（重名 / 校验失败）仍就地展示，不弹浮层", () => {
    expect(reportError(new ApiError("http", "已有同名技能", 409, null))).toBe(
      "已有同名技能",
    );
    expect(getToastSnapshot()).toEqual([]);
  });

  it("非 ApiError 的意外失败按就地展示处理，不猜它是连接问题", () => {
    expect(reportError(new Error("目录只读"))).toBe("目录只读");
    expect(getToastSnapshot()).toEqual([]);
  });

  it("同一句话并发投递只留一条（一次重挂载失败三个接口也不叠一排）", () => {
    const err = new ApiError("network", "无法连接后端服务", null, null);
    reportError(err);
    reportError(err);
    reportError(err);
    expect(getToastSnapshot()).toHaveLength(1);
  });
});
