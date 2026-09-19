/** 跨页面共用的小类型。 */

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
