/** 跨页面共用的小类型与错误呈现分流。 */
import { ApiError, errorMessage } from "../api";
import { toast } from "./toast";

/**
 * 一次性操作反馈（成功 / 错误提示）。
 *
 * 设置页的三个面板与提示词工作台都是「做一件事、给一条反馈、下一次操作覆盖它」，
 * 形状原本各抄一份；文案变体（如 `Alert` 的 variant 怎么映射）留给调用点，
 * 因为那是渲染差异，不在这里统一。
 */
export interface Feedback {
  kind: "success" | "error";
  text: string;
}

/**
 * 把一处失败分流成「就地展示」或「浮层提示」，返回需要就地展示的文字（浮层则返回 null）。
 *
 * 判据是这条信息要不要用户当场处理：连不上后端与等超时是**环境状态**——侧栏的服务状态点
 * 已经常驻表达它，页面上再铺一块红色提示条等于把临时状态焊在界面里，重新挂载一次还会再
 * 冒出来（2026-09-20 用户要求：不占界面位置，改用浮层提醒）。后端返回的业务错误（校验
 * 不通过、重名、并发冲突）是这一次操作的后果，用户要照着它改，必须留在原地。
 *
 * @param err - 捕获到的未知异常。
 * @returns 就地展示用的错误文字；已改由浮层提示时为 null。
 */
export function reportError(err: unknown): string | null {
  if (err instanceof ApiError && (err.kind === "network" || err.kind === "timeout")) {
    toast(errorMessage(err));
    // 既然连不上，侧栏那个点报的「运行中」当场就不成立了——广播一次让它重查（服务被脚本
    // 杀掉时页面收不到任何别的通知，这是唯一一条即时通路）。
    window.dispatchEvent(new Event("df:service-changed"));
    return null;
  }
  return errorMessage(err);
}
