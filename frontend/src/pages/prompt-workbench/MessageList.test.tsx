/** MessageList 渲染规则（PRD-0004）：附件封面降级链、空气泡抑制、AI 整框结构、流式状态收敛。 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MessageList } from "./MessageList";
import type { ChatMessage } from "./types";

function msg(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: 0,
    role: "user",
    text: "",
    attachment: null,
    partial: false,
    reasoning: null,
    reasoning_ms: null,
    elapsed_ms: null,
    ...overrides,
  } as ChatMessage;
}

const noop = vi.fn();

describe("MessageList 附件封面降级链（PRD-0004）", () => {
  it("当轮视频有封面：渲染封面图 + 播放角标，不出 <video>", () => {
    render(
      <MessageList
        messages={[
          msg({
            role: "user",
            text: "看这个",
            attachment: "clip.mp4",
            attachmentDataUrl: "data:video/mp4;base64,AA",
            attachmentPosterUrl: "data:image/jpeg;base64,PP",
            attachmentDurationSec: 12,
          }),
        ]}
        streaming={null}
        waitSeconds={0}
        copiedId={null}
        onCopy={noop}
        onPreview={noop}
      />,
    );
    const cover = screen.getByAltText("") as HTMLImageElement;
    expect(cover.src).toBe("data:image/jpeg;base64,PP");
    expect(document.querySelector("video")).toBeNull();
    expect(screen.getByText("0:12")).toBeInTheDocument();
  });

  it("历史视频无封面：用 <video #t=0.1> 现解码首帧（C0 口径）", () => {
    render(
      <MessageList
        messages={[
          msg({
            role: "user",
            text: "看这个",
            attachment: "clip.mp4",
            attachmentUrl: "/api/sessions/s1/attachments/clip.mp4",
          }),
        ]}
        streaming={null}
        waitSeconds={0}
        copiedId={null}
        onCopy={noop}
        onPreview={noop}
      />,
    );
    const video = document.querySelector("video") as HTMLVideoElement;
    expect(video).not.toBeNull();
    expect(video.src).toContain("#t=0.1");
    expect(document.querySelector("img")).toBeNull();
  });

  it("当轮视频没抽到封面：直接落胶片图标（同浏览器抽不出 = 解不了）", () => {
    render(
      <MessageList
        messages={[
          msg({
            role: "user",
            text: "",
            attachment: "clip.mov",
            attachmentDataUrl: "data:video/quicktime;base64,AA",
          }),
        ]}
        streaming={null}
        waitSeconds={0}
        copiedId={null}
        onCopy={noop}
        onPreview={noop}
      />,
    );
    expect(document.querySelector("video")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("svg.lucide-film")).not.toBeNull();
  });

  it(".avi/.mkv 识别为视频（与后端白名单同源），不再误判成图片", () => {
    render(
      <MessageList
        messages={[
          msg({
            role: "user",
            text: "",
            attachment: "demo.avi",
            attachmentUrl: "/api/sessions/s1/attachments/demo.avi",
          }),
        ]}
        streaming={null}
        waitSeconds={0}
        copiedId={null}
        onCopy={noop}
        onPreview={noop}
      />,
    );
    expect(document.querySelector("video")).not.toBeNull();
    expect(document.querySelector("svg.lucide-image")).toBeNull();
  });

  it("仅附件不打字：只显示缩略图，不出空气泡", () => {
    render(
      <MessageList
        messages={[
          msg({
            role: "user",
            text: "",
            attachment: "cat.png",
            attachmentDataUrl: "data:image/png;base64,AA",
          }),
        ]}
        streaming={null}
        waitSeconds={0}
        copiedId={null}
        onCopy={noop}
        onPreview={noop}
      />,
    );
    const stack = screen.getByRole("button", { name: "预览 cat.png" })
      .parentElement as HTMLElement;
    expect(stack.children).toHaveLength(1);
  });

  it("点击缩略图把预览目标交给 onPreview（当轮带封面）", async () => {
    const onPreview = vi.fn();
    render(
      <MessageList
        messages={[
          msg({
            role: "user",
            text: "",
            attachment: "clip.mp4",
            attachmentDataUrl: "data:video/mp4;base64,AA",
            attachmentPosterUrl: "data:image/jpeg;base64,PP",
          }),
        ]}
        streaming={null}
        waitSeconds={0}
        copiedId={null}
        onCopy={noop}
        onPreview={onPreview}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "预览 clip.mp4" }));
    expect(onPreview).toHaveBeenCalledWith({
      url: "data:video/mp4;base64,AA",
      kind: "video",
      name: "clip.mp4",
      posterUrl: "data:image/jpeg;base64,PP",
    });
  });
});

describe("MessageList AI 整框结构（原型 v20 .ai-box）", () => {
  it("思考过程与正文同处一个白底整框：正文气泡不在带边框灰底块里", () => {
    render(
      <MessageList
        messages={[
          msg({
            role: "assistant",
            text: "打标结果",
            reasoning: "先想想",
          }),
        ]}
        streaming={null}
        waitSeconds={0}
        copiedId={null}
        onCopy={noop}
        onPreview={noop}
      />,
    );
    const text = screen.getByText("打标结果");
    const box = text.closest(".bg-card") as HTMLElement;
    expect(box).not.toBeNull();
    // 同框：思考过程折叠区与正文在同一个整框内。
    expect(box.querySelector("summary")?.textContent).toContain("思考过程");
  });

  it("仅思考无正文的半截消息：不出空气泡、不出悬空分隔线", () => {
    render(
      <MessageList
        messages={[
          msg({
            role: "assistant",
            text: "",
            partial: true,
            reasoning: "只想到一半",
          }),
        ]}
        streaming={null}
        waitSeconds={0}
        copiedId={null}
        onCopy={noop}
        onPreview={noop}
      />,
    );
    expect(screen.getByText(/未完成 · 生成中断/)).toBeInTheDocument();
    const summary = screen.getByText("思考过程").closest("details");
    expect(summary?.querySelector(".mx-3\\.5")).toBeNull();
  });
});

describe("MessageList 流式状态收敛（PRD-0004）", () => {
  it("首个增量到达前：只有「响应中 · 已用时」，无「生成中」meta 行", () => {
    render(
      <MessageList
        messages={[]}
        streaming={{ reasoning: "", content: "" }}
        waitSeconds={4}
        copiedId={null}
        onCopy={noop}
        onPreview={noop}
      />,
    );
    expect(screen.getByText(/响应中 · 已用时 4s/)).toBeInTheDocument();
    expect(screen.queryByText(/生成中 · 已用时/)).not.toBeInTheDocument();
  });

  it("正文已到：气泡 + meta 行「生成中 · 已用时 · 字数」，占位行消失", () => {
    render(
      <MessageList
        messages={[]}
        streaming={{ reasoning: "想", content: "半截" }}
        waitSeconds={7}
        copiedId={null}
        onCopy={noop}
        onPreview={noop}
      />,
    );
    expect(screen.queryByText(/响应中 · 已用时/)).not.toBeInTheDocument();
    expect(screen.getByText(/生成中 · 已用时 7s · 2 字/)).toBeInTheDocument();
    expect(screen.getByText(/半截/)).toBeInTheDocument();
  });
});
