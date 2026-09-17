import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api, type TaskView } from "../../api";
import { RelocateWorkdirDialog } from "./RelocateWorkdirDialog";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: {
    relocateWorkdir: vi.fn(),
    getTask: vi.fn(),
    cancelTask: vi.fn(),
    listDirectory: vi.fn(),
  },
}));

const completed: TaskView = {
  id: "move-1",
  status: "succeeded",
  progress: 1,
  error: null,
  result: { path: "/srv/new", old_path: "/srv/old", cleanup_pending: true },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.relocateWorkdir).mockResolvedValue({ task_id: "move-1" });
  vi.mocked(api.getTask).mockResolvedValue(completed);
});

it("二次确认才搬迁，网络错误重查原句柄并展示旧位置残留", async () => {
  const user = userEvent.setup();
  const changed = vi.fn();
  vi.mocked(api.getTask).mockRejectedValueOnce(new Error("连接中断"));
  render(
    <RelocateWorkdirDialog
      wid="w1"
      source="/srv/old"
      initialTarget="/srv/new"
      onClose={vi.fn()}
      onChanged={changed}
    />,
  );

  await user.click(screen.getByRole("button", { name: "修改路径" }));
  expect(api.relocateWorkdir).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "确认搬迁" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("连接中断");
  expect(changed).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "重新查询" }));

  expect(await screen.findByText("旧位置尚未清理：/srv/old")).toBeVisible();
  expect(api.relocateWorkdir).toHaveBeenCalledExactlyOnceWith("w1", "/srv/new");
  expect(api.getTask).toHaveBeenNthCalledWith(2, "move-1");
  expect(changed).toHaveBeenCalledOnce();
});

it("任务丢失回到空闲并刷新持久状态，不自动重新搬迁", async () => {
  const user = userEvent.setup();
  const changed = vi.fn();
  vi.mocked(api.getTask).mockRejectedValueOnce(
    new ApiError("http", "任务不存在", 404, null),
  );
  render(
    <RelocateWorkdirDialog
      wid="w1"
      source="/srv/old"
      initialTarget="/srv/new"
      onClose={vi.fn()}
      onChanged={changed}
    />,
  );

  await user.click(screen.getByRole("button", { name: "修改路径" }));
  await user.click(screen.getByRole("button", { name: "确认搬迁" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("上次任务已丢失");
  expect(screen.getByRole("textbox", { name: "目标目录" })).toBeEnabled();
  expect(api.relocateWorkdir).toHaveBeenCalledOnce();
  expect(changed).toHaveBeenCalledOnce();
});

it("成功载荷无效时保留句柄，修正响应后重查成功", async () => {
  const user = userEvent.setup();
  const changed = vi.fn();
  vi.mocked(api.getTask).mockResolvedValueOnce({ ...completed, result: {} });
  render(
    <RelocateWorkdirDialog
      wid="w1"
      source="/srv/old"
      initialTarget="/srv/new"
      onClose={vi.fn()}
      onChanged={changed}
    />,
  );

  await user.click(screen.getByRole("button", { name: "修改路径" }));
  await user.click(screen.getByRole("button", { name: "确认搬迁" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("搬迁结果格式无效");
  expect(changed).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "重新查询" }));

  expect(await screen.findByText("已搬迁到 /srv/new")).toBeVisible();
  expect(changed).toHaveBeenCalledOnce();
  expect(api.relocateWorkdir).toHaveBeenCalledOnce();
});

it.each([
  {
    system: "Linux",
    source: "/srv/a\\b",
    parent: "/target\\",
    expected: "/target\\/a\\b",
  },
  {
    system: "Windows",
    source: "C:\\data\\images",
    parent: "D:\\",
    expected: "D:\\images",
  },
  {
    system: "Windows",
    source: "C:/data/images",
    parent: "\\\\host\\share",
    expected: "\\\\host\\share\\images",
  },
])(
  "按后端 $system 语义拼接目标 $expected",
  async ({ system, source, parent, expected }) => {
    const user = userEvent.setup();
    vi.mocked(api.listDirectory).mockResolvedValue({
      system,
      hostname: "server",
      path: parent,
      parent: null,
      entries: [],
      unavailable_count: 0,
    });
    render(
      <RelocateWorkdirDialog
        wid="w1"
        source={source}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "选择目标父目录" }));
    const picker = within(screen.getByRole("dialog", { name: "选择目录" }));
    await picker.findByText("没有匹配的项目");
    await user.click(picker.getByRole("button", { name: "选择此目录" }));

    expect(screen.getByRole("textbox", { name: "目标目录" })).toHaveValue(expected);
    expect(api.relocateWorkdir).not.toHaveBeenCalled();
  },
);

it("取消失败允许重试，取消请求受理后等待真实终态", async () => {
  const user = userEvent.setup();
  const changed = vi.fn();
  const running: TaskView = {
    ...completed,
    status: "running",
    result: null,
    progress: 0.2,
  };
  vi.mocked(api.getTask).mockResolvedValue(running);
  vi.mocked(api.cancelTask)
    .mockRejectedValueOnce(new Error("取消请求中断"))
    .mockResolvedValue(running);
  render(
    <RelocateWorkdirDialog
      wid="w1"
      source="/srv/old"
      initialTarget="/srv/new"
      onClose={vi.fn()}
      onChanged={changed}
    />,
  );
  await user.click(screen.getByRole("button", { name: "修改路径" }));
  await user.click(screen.getByRole("button", { name: "确认搬迁" }));
  await user.click(await screen.findByRole("button", { name: "取消搬迁" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("取消请求中断");
  expect(changed).not.toHaveBeenCalled();
  vi.mocked(api.getTask).mockResolvedValue({ ...running, status: "cancelled" });
  await user.click(screen.getByRole("button", { name: "取消搬迁" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("搬迁已取消");
  expect(api.cancelTask).toHaveBeenCalledTimes(2);
  expect(changed).toHaveBeenCalledOnce();
});
