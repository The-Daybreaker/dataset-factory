/** 提示词工作台跨文件共享的数据形状。 */
import type { HistoryMessageView } from "../../api";

/** 待发送的附件（图片或视频，一期单素材/次）：原始文件名 + data URL + 视频抽帧参数。 */
export interface PendingMedia {
  name: string;
  dataUrl: string;
  kind: "image" | "video";
  /** MIME 类型与字节数（来自 File 对象，待发卡片的信息行用）。 */
  mime: string;
  byteSize: number;
  fps: number;
  maxFrames: number;
}

/** 界面里的消息 = 后端历史消息 + 渲染用稳定 id + 新增消息才有的 meta。 */
export interface ChatMessage extends HistoryMessageView {
  id: number;
  model?: string;
  durationSeconds?: number;
  createdAt?: Date;
  /** 本轮思考过程（仅本轮打标产生、只存页面内存不落盘——历史恢复的消息没有它）。 */
  reasoning?: string;
  /** 本轮发送的附件字节（data URL，仅当轮消息有；历史恢复只有文件名、渲染图标占位）。 */
  attachmentDataUrl?: string;
}
