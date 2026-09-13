/**
 * api.ts 的错误分类测试。
 *
 * 为什么值得测：这层是「错误按层展示」的唯一出口——网络断、超时、后端报错三种
 * 情况在界面上要有各自的说法。mock fetch 就能模拟这三种失败，不必真起后端。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, errorMessage } from "./api";

// fetch 永远 pending 且遵循 abort 信号：用来测「超时主动放弃」这条路径。
function stubPendingFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_path: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("The operation was aborted.", "AbortError"));
          });
        }),
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("request 错误分类", () => {
  it("连不上后端 → network 错误，提示检查 dsf serve", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(api.listPrompts()).rejects.toMatchObject({
      kind: "network",
      status: null,
    });
  });

  it("等待超时 → timeout 错误，消息含等待秒数", async () => {
    vi.useFakeTimers();
    stubPendingFetch();

    const pending = api.listPrompts();
    const assertion = expect(pending).rejects.toMatchObject({ kind: "timeout" });
    await vi.advanceTimersByTimeAsync(16_000); // 越过 15s 超时阈值
    await assertion;
  });

  it("后端报错 → http 错误，透传 detail 并附上请求 id", async () => {
    const response = new Response(JSON.stringify({ detail: "模型调用失败" }), {
      status: 502,
      headers: { "Content-Type": "application/json", "X-Request-ID": "abc123def" },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    const error = await api.listPrompts().then(
      () => {
        throw new Error("应当抛错");
      },
      (err: unknown) => err,
    );
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.kind).toBe("http");
    expect(apiError.status).toBe(502);
    expect(apiError.requestId).toBe("abc123def");
    expect(apiError.message).toContain("模型调用失败");
    expect(apiError.message).toContain("abc123def");
  });

  it("errorMessage 对 ApiError 直接给出消息文本（可直接展示）", async () => {
    const response = new Response("{}", { status: 404 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(api.getPrompt("不存在")).rejects.toThrow();
    await api.getPrompt("不存在").catch((err: unknown) => {
      expect(errorMessage(err)).toContain("HTTP 404");
    });
  });
});

describe("labelStream SSE 解析", () => {
  it("SSE 帧按事件分发到回调（start / delta / done）", async () => {
    const encoder = new TextEncoder();
    const sse =
      'event: start\ndata: {"session_id":"s1"}\n\n' +
      'event: delta\ndata: {"kind":"reasoning","text":"想一想"}\n\n' +
      'event: delta\ndata: {"kind":"content","text":"你好"}\n\n' +
      'event: done\ndata: {"session_id":"s1","caption":"你好"}\n\n';
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(sse));
        controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(body, { status: 200 })),
    );
    const calls: string[] = [];

    await api.labelStream(
      {
        prompt_name: "p",
        instruction: "写",
        image_name: "image.png",
        video_name: "video.mp4",
        video_fps: 2.0,
        video_max_frames: 16,
      },
      {
        onStart: (id) => calls.push(`start:${id}`),
        onDelta: (kind, text) => calls.push(`delta:${kind}:${text}`),
        onDone: (id, caption) => calls.push(`done:${id}:${caption}`),
        onError: (message) => calls.push(`error:${message}`),
      },
    );

    expect(calls).toEqual([
      "start:s1",
      "delta:reasoning:想一想",
      "delta:content:你好",
      "done:s1:你好",
    ]);
  });

  it("HTTP 层错误（预备段 4xx）→ 抛 ApiError，不走事件回调", async () => {
    const response = new Response(JSON.stringify({ detail: "提示词不存在" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const onError = vi.fn();

    await expect(
      api.labelStream(
        {
          prompt_name: "缺失",
          instruction: "x",
          image_name: "image.png",
          video_name: "video.mp4",
          video_fps: 2.0,
          video_max_frames: 16,
        },
        {
          onStart: vi.fn(),
          onDelta: vi.fn(),
          onDone: vi.fn(),
          onError,
        },
      ),
    ).rejects.toMatchObject({ kind: "http", status: 404 });
    expect(onError).not.toHaveBeenCalled();
  });
});
