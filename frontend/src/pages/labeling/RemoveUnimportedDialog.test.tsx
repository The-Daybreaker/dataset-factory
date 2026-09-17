import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api";
import { RemoveUnimportedDialog } from "./RemoveUnimportedDialog";

vi.mock("../../api", () => ({
  api: { removeUnimported: vi.fn() },
  errorMessage: (error: unknown) => String(error),
}));
beforeEach(() => vi.clearAllMocks());

it("取消不提交删除", async () => {
  const onClose = vi.fn();
  const user = userEvent.setup();
  render(
    <RemoveUnimportedDialog
      wid="one"
      name="a.jpg"
      onClose={onClose}
      onRemoved={vi.fn()}
    />,
  );

  await user.click(screen.getByRole("button", { name: "取消" }));

  expect(onClose).toHaveBeenCalledOnce();
  expect(api.removeUnimported).not.toHaveBeenCalled();
});

it("请求失败保持确认内容且允许重试", async () => {
  const onRemoved = vi.fn();
  vi.mocked(api.removeUnimported)
    .mockRejectedValueOnce(new Error("目录正在导入"))
    .mockResolvedValueOnce({ count: 1, recovery_path: "/recovery/a" });
  const user = userEvent.setup();
  render(
    <RemoveUnimportedDialog
      wid="one"
      name="a.jpg"
      onClose={vi.fn()}
      onRemoved={onRemoved}
    />,
  );

  await user.click(screen.getByRole("button", { name: "删除" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("目录正在导入");
  expect(onRemoved).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "删除" }));

  expect(await screen.findByRole("status")).toHaveTextContent("/recovery/a");
  expect(onRemoved).toHaveBeenCalledOnce();
  expect(api.removeUnimported).toHaveBeenCalledTimes(2);
});

it("卸载后的迟到结果不刷新另一个目录", async () => {
  let resolve!: (value: { count: number; recovery_path: string }) => void;
  vi.mocked(api.removeUnimported).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const onRemoved = vi.fn();
  const user = userEvent.setup();
  const rendered = render(
    <RemoveUnimportedDialog
      wid="one"
      name="a.jpg"
      onClose={vi.fn()}
      onRemoved={onRemoved}
    />,
  );
  const dialog = within(screen.getByRole("dialog"));
  await user.click(dialog.getByRole("button", { name: "删除" }));
  expect(dialog.getByRole("button", { name: "删除中" })).toBeDisabled();
  rendered.unmount();

  await act(async () => resolve({ count: 1, recovery_path: "/recovery/a" }));

  expect(onRemoved).not.toHaveBeenCalled();
});
