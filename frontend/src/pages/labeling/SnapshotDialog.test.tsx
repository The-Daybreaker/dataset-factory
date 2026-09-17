import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api";
import type { components } from "../../api-types.gen";
import { SnapshotDialog } from "./SnapshotDialog";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: { getBatchSnapshot: vi.fn() },
}));

const snapshot: components["schemas"]["BatchSnapshotView"] = {
  endpoint: {
    name: "main",
    base_url: "https://api.example.com/v1",
    model: "test-model",
    api_format: "chat_completions",
    request_params: { temperature: 0.7 },
    sha256: "endpoint-hash",
  },
  prompt: { name: "描述", body: "应用时的提示词正文", sha256: "prompt-hash" },
  skills: [{ name: "caption", body: "应用时的技能正文", sha256: "skill-hash" }],
  built_at: "2026-09-17T00:00:00Z",
  tool_version: "0.1.0",
  sha256: "current-hash",
  recorded_sha256: "current-hash",
  changed: false,
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getBatchSnapshot).mockResolvedValue(snapshot);
});

it("展示已保存的全文与参数，相同哈希不显示警告并可复制", async () => {
  const user = userEvent.setup();
  const clipboard = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  render(<SnapshotDialog wid="work" batch="s1" onClose={() => {}} />);
  expect(await screen.findByText("应用时的提示词正文")).toBeInTheDocument();
  expect(screen.getByText("应用时的技能正文")).toBeInTheDocument();
  expect(screen.getByText("test-model")).toBeInTheDocument();
  expect(screen.getByText("0.7")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "复制快照" }));
  expect(clipboard).toHaveBeenCalledWith(JSON.stringify(snapshot, null, 2));
  expect(await screen.findByRole("status")).toHaveTextContent("已复制");
});

it("快照被手改时展示警告，正文与关闭操作仍可用", async () => {
  const user = userEvent.setup();
  const close = vi.fn();
  vi.mocked(api.getBatchSnapshot).mockResolvedValue({
    ...snapshot,
    changed: true,
    recorded_sha256: "old-hash",
  });
  render(<SnapshotDialog wid="work" batch="s1" onClose={close} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "快照哈希与最近一次运行记录不一致",
  );
  expect(screen.getByText("应用时的提示词正文")).toBeInTheDocument();
  await user.click(screen.getByText("关闭", { selector: "button" }));
  expect(close).toHaveBeenCalled();
});

it("读取失败可重试同一快照而不写入任何配置", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getBatchSnapshot).mockRejectedValueOnce(new Error("读取失败"));
  render(<SnapshotDialog wid="work" batch="s1" onClose={() => {}} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("读取失败");
  await user.click(screen.getByRole("button", { name: "重新读取" }));
  expect(await screen.findByText("应用时的提示词正文")).toBeInTheDocument();
  expect(api.getBatchSnapshot).toHaveBeenCalledTimes(2);
  expect(api.getBatchSnapshot).toHaveBeenLastCalledWith("work", "s1");
});

it("切换批次后丢弃旧快照迟到结果", async () => {
  let finish: ((value: typeof snapshot) => void) | undefined;
  vi.mocked(api.getBatchSnapshot).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { rerender } = render(
    <SnapshotDialog wid="work" batch="s1" onClose={() => {}} />,
  );
  rerender(<SnapshotDialog wid="work" batch="s2" onClose={() => {}} />);
  expect(await screen.findByText("应用时的提示词正文")).toBeInTheDocument();
  await act(async () => {
    finish?.({ ...snapshot, prompt: { ...snapshot.prompt, body: "旧批次正文" } });
  });
  expect(screen.queryByText("旧批次正文")).not.toBeInTheDocument();
});
