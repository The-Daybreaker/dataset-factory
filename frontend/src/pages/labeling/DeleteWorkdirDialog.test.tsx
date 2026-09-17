import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api";
import { DeleteWorkdirDialog } from "./DeleteWorkdirDialog";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: { previewWorkdirDeletion: vi.fn(), deleteWorkdir: vi.fn() },
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.previewWorkdirDeletion).mockResolvedValue({
    path: "/srv/original",
    file_count: 9,
    total_bytes: 2048,
    original_materials: true,
    confirmation: "此工作目录即原始素材目录，删除后不可恢复。",
  });
});

it("展示原始素材风险，确认后提交预览路径，真正成功才退出", async () => {
  const user = userEvent.setup();
  const deleted = vi.fn();
  vi.mocked(api.deleteWorkdir).mockResolvedValue({
    deleted: true,
    remaining_path: null,
  });
  render(<DeleteWorkdirDialog wid="w1" onClose={vi.fn()} onDeleted={deleted} />);

  expect(
    await screen.findByText("此工作目录即原始素材目录，删除后不可恢复。"),
  ).toBeVisible();
  expect(api.deleteWorkdir).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "确认删除" }));

  expect(api.deleteWorkdir).toHaveBeenCalledExactlyOnceWith("w1", "/srv/original");
  expect(deleted).toHaveBeenCalledOnce();
});

it("部分删除保留残留位置，重新读取范围后才可再次确认", async () => {
  const user = userEvent.setup();
  const deleted = vi.fn();
  vi.mocked(api.deleteWorkdir).mockResolvedValueOnce({
    deleted: false,
    remaining_path: "/srv/original",
  });
  render(<DeleteWorkdirDialog wid="w1" onClose={vi.fn()} onDeleted={deleted} />);
  await screen.findByText("/srv/original");
  await user.click(screen.getByRole("button", { name: "确认删除" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("残留目录：/srv/original");
  expect(screen.getByRole("button", { name: "确认删除" })).toBeDisabled();
  expect(deleted).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "重新查看" }));

  expect(api.previewWorkdirDeletion).toHaveBeenCalledTimes(2);
  expect(await screen.findByText("/srv/original")).toBeVisible();
  expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled();
  expect(api.deleteWorkdir).toHaveBeenCalledTimes(1);
});
