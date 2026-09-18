import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import type { components } from "../../api-types.gen";
import { TooltipProvider } from "../../components/ui/tooltip";
import { StrategyToolbar } from "./StrategyToolbar";

const mocks = vi.hoisted(() => ({
  listStrategies: vi.fn(),
  listStrategyReferences: vi.fn(),
  createStrategy: vi.fn(),
  updateStrategy: vi.fn(),
  copyStrategy: vi.fn(),
  deleteStrategy: vi.fn(),
  rebindStrategy: vi.fn(),
}));
vi.mock("../../api", () => ({
  api: mocks,
  errorMessage: (error: unknown) =>
    error instanceof Error ? error.message : String(error),
}));
const strategy: components["schemas"]["StrategyView"] = {
  id: "a1",
  name: "详细描述",
  description: "训练用",
  endpoint: "default",
  prompt: "caption",
  skills: [],
  available: true,
  missing_refs: [],
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};
const select = vi.fn<() => Promise<void>>();
function mount(strict = false): void {
  const content = (
    <TooltipProvider>
      <StrategyToolbar
        references={{ endpoint: "default", prompt: "caption", skills: [] }}
        onOpenSettings={vi.fn()}
        prompts={[{ name: "caption", description: "" }]}
        endpoints={[]}
        skills={[]}
        locked={false}
        onSelect={select}
      />
    </TooltipProvider>
  );
  render(strict ? <StrictMode>{content}</StrictMode> : content);
}
beforeEach(() => {
  vi.resetAllMocks();
  mocks.listStrategies.mockResolvedValue([strategy]);
  mocks.listStrategyReferences.mockResolvedValue([]);
  select.mockResolvedValue(undefined);
});

it("StrictMode 下关闭重开菜单后忽略旧列表响应", async () => {
  let resolveOld!: (entries: (typeof strategy)[]) => void;
  mocks.listStrategies
    .mockReturnValueOnce(
      new Promise<(typeof strategy)[]>((resolve) => {
        resolveOld = resolve;
      }),
    )
    .mockResolvedValueOnce([strategy]);
  mount(true);
  await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
  await userEvent.keyboard("{Escape}");
  await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
  await screen.findByRole("button", { name: /详细描述.*训练用/ });

  await act(async () => resolveOld([]));

  expect(screen.getByRole("button", { name: /详细描述.*训练用/ })).toBeInTheDocument();
});

it("公用配置行展示端点、提示词与高级参数，前往设置可点", async () => {
  const onOpenSettings = vi.fn();
  const content = (
    <TooltipProvider>
      <StrategyToolbar
        references={{ endpoint: "default", prompt: "caption", skills: [] }}
        onOpenSettings={onOpenSettings}
        prompts={[{ name: "caption", description: "" }]}
        endpoints={[
          {
            api_format: "openai-chat",
            base_url: "https://api.example.com/v1",
            has_api_key: true,
            is_active: true,
            model: "test-model",
            name: "default",
            request_params: {
              extra_body: null,
              max_retries: null,
              max_tokens: 8192,
              temperature: 0.7,
              timeout_seconds: null,
              top_p: null,
            },
          },
        ]}
        skills={[]}
        locked={false}
        onSelect={select}
      />
    </TooltipProvider>
  );
  render(content);
  expect(await screen.findByText("default")).toBeInTheDocument();
  expect(screen.getByText("caption")).toBeInTheDocument();
  expect(screen.getByText(/temperature 0\.7 · max_tokens 8192/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "前往设置" }));
  expect(onOpenSettings).toHaveBeenCalledOnce();
});

it("选中策略后显示被多少批次应用的出身提示", async () => {
  const user = userEvent.setup();
  mocks.listStrategyReferences.mockResolvedValue([
    {
      workdir_id: "w1",
      workdir_title: "人物 · 白裙",
      seq: 1,
      batch_name: "详细描述A",
    },
  ]);
  mount();
  await user.click(screen.getByRole("button", { name: "切换策略" }));
  await user.click(await screen.findByRole("button", { name: /详细描述.*训练用/ }));
  expect(await screen.findByText(/已被 1 个批次应用/)).toBeInTheDocument();
});

it("没有选中策略时不显示出身提示", () => {
  mount();
  expect(screen.queryByText(/已被 .* 个批次应用/)).not.toBeInTheDocument();
});

it("删除失败的原因在确认弹窗内可见，重试仍使用原策略", async () => {
  mocks.deleteStrategy
    .mockRejectedValueOnce(new Error("目录只读"))
    .mockResolvedValueOnce(undefined);
  mount();
  await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "删除策略 详细描述" }),
  );
  const dialog = within(screen.getByRole("dialog"));

  await userEvent.click(dialog.getByRole("button", { name: "删除" }));
  expect(await dialog.findByText("目录只读")).toBeVisible();
  await userEvent.click(dialog.getByRole("button", { name: "删除" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(mocks.deleteStrategy).toHaveBeenNthCalledWith(2, "a1");
});

it("编辑策略后锁定切换，保存失败保留草稿，重试成功才解锁", async () => {
  mocks.updateStrategy
    .mockRejectedValueOnce(new Error("写入失败"))
    .mockResolvedValueOnce({ ...strategy, name: "新名字" });
  mount();
  await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
  await userEvent.click(
    await screen.findByRole("button", {
      name: /详细描述.*训练用/,
    }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("策略名称")).toHaveValue("详细描述"),
  );

  fireEvent.change(screen.getByLabelText("策略名称"), { target: { value: "新名字" } });
  expect(screen.getByRole("button", { name: "切换策略" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "保存策略" }));
  expect(await screen.findByText("写入失败")).toBeInTheDocument();
  expect(screen.getByLabelText("策略名称")).toHaveValue("新名字");
  await userEvent.click(screen.getByRole("button", { name: "保存策略" }));

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "切换策略" })).toBeEnabled(),
  );
  expect(mocks.updateStrategy).toHaveBeenLastCalledWith("a1", {
    name: "新名字",
    description: "训练用",
    endpoint: "default",
    prompt: "caption",
    skills: [],
  });
  expect(select).toHaveBeenCalledTimes(1);
});

it("失效策略进入重新指定，不应用缺失引用；删除需要二次确认", async () => {
  mocks.listStrategies.mockResolvedValue([
    { ...strategy, available: false, missing_refs: ["提示词已不存在"] },
  ]);
  mocks.deleteStrategy.mockResolvedValue(undefined);
  mount();
  await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
  await userEvent.click(
    await screen.findByRole("button", {
      name: /详细描述.*训练用.*引用缺失/,
    }),
  );
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(select).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "取消" }));
  await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
  await userEvent.click(screen.getByRole("button", { name: "删除策略 详细描述" }));
  expect(mocks.deleteStrategy).not.toHaveBeenCalled();
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "删除" }),
  );
  await waitFor(() => expect(mocks.deleteStrategy).toHaveBeenCalledWith("a1"));
});
