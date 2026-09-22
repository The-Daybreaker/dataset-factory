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
  /** 视频首帧封面（data URL，L26/V8）：挑选附件时本地抽帧，气泡与卡片共用。 */
  posterUrl?: string;
  /** 视频时长秒数（气泡时长角标用；拿不到时缺省）。 */
  durationSec?: number;
}

/** 界面里的消息 = 后端历史消息 + 渲染用稳定 id + 新增消息才有的 meta。 */
export interface ChatMessage extends HistoryMessageView {
  id: number;
  model?: string;
  durationSeconds?: number;
  /** 本轮思考耗时秒数（V7：思考开关的仪表盘；本地计时，仅新消息有）。 */
  reasoningSeconds?: number;
  createdAt?: Date;
  /** 本轮发送的附件字节（data URL，仅当轮消息有）。 */
  attachmentDataUrl?: string;
  /** 本轮视频附件的封面（发送时本地抽帧所得的 data URL，仅当轮消息有）。 */
  attachmentPosterUrl?: string;
  /** 历史附件的字节地址（B5：直连会话附件端点，刷新后仍能显示）。 */
  attachmentUrl?: string;
  /** 视频附件的时长秒数（仅当轮消息有，时长角标用）。 */
  attachmentDurationSec?: number;
}
