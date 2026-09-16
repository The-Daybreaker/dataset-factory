/** 设置页各子面板共用的小类型。 */

/** 一次性操作反馈（成功 / 错误提示）。 */
export interface Feedback {
  kind: "success" | "error";
  text: string;
}
