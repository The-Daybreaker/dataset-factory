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
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { components } from "../../api-types.gen";
import { TooltipProvider } from "../../components/ui/tooltip";
import { StrategyToolbar } from "./StrategyToolbar";

const mocks = vi.hoisted(() => ({
  listStrategies: vi.fn(),
  createStrategy: vi.fn(),
  updateStrategy: vi.fn(),
  copyStrategy: vi.fn(),
  deleteStrategy: vi.fn(),
  rebindStrategy: vi.fn(),
}));
// 部分 mock：只替掉 api 对象，ApiError / errorMessage 用真货——错误分档要靠真类的 kind 字段判。
vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: mocks,
}));
const strategy: components["schemas"]["StrategyView"] = {
  id: "a1",
  name: "详细描述",
  description: "训练用",
  endpoint: "default",
  prompt: "caption",
  skills: [],
  body_chars: 12_400,
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
        prompts={[{ name: "caption", description: "" }]}
        endpoints={[]}
        skills={[]}
        locked={false}
        onSelect={select}
        onNewStrategy={() => {}}
      />
    </TooltipProvider>
  );
  render(strict ? <StrictMode>{content}</StrictMode> : content);
}
beforeEach(() => {
  vi.resetAllMocks();
  mocks.listStrategies.mockResolvedValue([strategy]);
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

it("编辑策略后锁定切换：列表照开、点了才提示；保存失败保留草稿，重试成功才解锁", async () => {
  const second: components["schemas"]["StrategyView"] = {
    ...strategy,
    id: "a2",
    name: "极简描述",
  };
  mocks.listStrategies.mockResolvedValue([strategy, second]);
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
  // L9（2026-09-21 审计 / 原型 :1084-1093）：锁定时列表照常打开，选中**非当前项**
  // 才就地提示原因——不再是「按钮灰掉、列表也打不开」。重开菜单点另一套策略。
  await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
  await userEvent.click(
    await screen.findByRole("button", { name: /极简描述.*训练用/ }),
  );
  expect(await screen.findByText(/先点「保存」，才能切换策略/)).toBeVisible();
  // 菜单开着时外部元素被 aria-hidden（浮层陷阱），用 Escape 收起再继续。
  await userEvent.keyboard("{Escape}");

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
it("下拉行给出注入字数，悬停可见出身三参数", async () => {
  mount();
  await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
  const row = await screen.findByRole("button", { name: /^详细描述/ });

  expect(row).toHaveTextContent("1.2 万字");

  await userEvent.hover(row);
  const tip = await screen.findByRole("tooltip");
  expect(tip).toHaveTextContent("端点");
  expect(tip).toHaveTextContent("default");
  expect(tip).toHaveTextContent("提示词");
  expect(tip).toHaveTextContent("caption");
  expect(tip).toHaveTextContent("Skill");
});

describe("策略选中的启动恢复与镜像（三期 v2）", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("镜像按 id 恢复选中：重启后不再是新建策略，名称/描述用镜像缓冲", async () => {
    localStorage.setItem(
      "dsf-workbench-strategy",
      JSON.stringify({
        id: "a1",
        name: "详细描述（未保存改名）",
        description: "训练用",
        endpoint: "default",
        prompt: "caption",
        skills: [],
      }),
    );
    mount();

    await waitFor(() =>
      expect(screen.getByLabelText("策略名称")).toHaveValue("详细描述（未保存改名）"),
    );
    expect(screen.getByLabelText("策略描述")).toHaveValue("训练用");
    // 恢复不触碰门闩：镜像原样保留（不会被挂载首帧的空白冲掉）。
    const stored = JSON.parse(localStorage.getItem("dsf-workbench-strategy") ?? "null");
    expect(stored?.id).toBe("a1");
  });

  it("无镜像时按签名认领：当前工作配置正是一个策略，就把它认回来", async () => {
    // mount() 的 references = {endpoint: "default", prompt: "caption", skills: []}
    // 与库里唯一策略的签名一致——启动即认领，不再显示「新建策略」。
    mount();

    await waitFor(() =>
      expect(screen.getByLabelText("策略名称")).toHaveValue("详细描述"),
    );
  });

  it("镜像指向已删除的策略：保持新建态，不按签名认领", async () => {
    localStorage.setItem(
      "dsf-workbench-strategy",
      JSON.stringify({
        id: "ghost",
        name: "已删除的策略",
        description: "",
        endpoint: "default",
        prompt: "caption",
        skills: [],
      }),
    );
    mount();

    await waitFor(() => expect(mocks.listStrategies).toHaveBeenCalled());
    await expect(screen.getByLabelText("策略名称")).toHaveValue("");
  });

  it("新建策略回调：离开当前配置时通知工作域清会话", async () => {
    const onNewStrategy = vi.fn();
    localStorage.setItem(
      "dsf-workbench-strategy",
      JSON.stringify({
        id: "a1",
        name: "详细描述",
        description: "训练用",
        endpoint: "default",
        prompt: "caption",
        skills: [],
      }),
    );
    render(
      <TooltipProvider>
        <StrategyToolbar
          references={{ endpoint: "default", prompt: "caption", skills: [] }}
          prompts={[{ name: "caption", description: "" }]}
          endpoints={[]}
          skills={[]}
          locked={false}
          onSelect={select}
          onNewStrategy={onNewStrategy}
        />
      </TooltipProvider>,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("策略名称")).toHaveValue("详细描述"),
    );

    await userEvent.click(screen.getByRole("button", { name: "切换策略" }));
    await userEvent.click(screen.getByRole("button", { name: "新建策略" }));

    expect(onNewStrategy).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("策略名称")).toHaveValue("");
    // 明确离开：镜像如实落 null，重启不再认领旧策略。
    expect(localStorage.getItem("dsf-workbench-strategy")).toBe("null");
  });
});
