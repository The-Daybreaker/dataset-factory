import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api";
import { NewStrategyDialog } from "./NewStrategyDialog";

vi.mock("../../api", async (original) => ({
  ...(await original<typeof import("../../api")>()),
  api: {
    listStrategies: vi.fn(),
    listEndpoints: vi.fn(),
    listPrompts: vi.fn(),
    listSkills: vi.fn(),
    createBatch: vi.fn(),
    startRun: vi.fn(),
  },
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listStrategies).mockResolvedValue([]);
  vi.mocked(api.listSkills).mockResolvedValue([]);
  vi.mocked(api.listPrompts).mockResolvedValue([{ name: "caption", description: "" }]);
  vi.mocked(api.listEndpoints).mockResolvedValue([
    {
      name: "model",
      model: "vision",
      is_active: true,
      has_api_key: true,
      base_url: "https://example.invalid",
      api_format: "openai-chat-completions",
      request_params: {},
    },
  ]);
  vi.mocked(api.createBatch).mockResolvedValue({
    id: "s4",
    seq: 4,
    name: "新策略",
    description: "",
    active: true,
    created_at: "",
    product_count: 0,
  });
});

it("从库应用提交库 ID，读取失败后可以重试", async () => {
  const user = userEvent.setup();
  const onCreated = vi.fn();
  vi.mocked(api.listStrategies).mockResolvedValue([
    {
      id: "lib1",
      name: "库策略",
      description: "",
      endpoint: "model",
      prompt: "caption",
      skills: [],
      available: true,
      missing_refs: [],
      created_at: "",
      updated_at: "",
    },
  ]);
  vi.mocked(api.listPrompts).mockRejectedValueOnce(new Error("配置读取失败"));
  render(
    <NewStrategyDialog
      wid="work"
      title="素材"
      onClose={vi.fn()}
      onCreated={onCreated}
    />,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent("配置读取失败");
  await user.click(screen.getByRole("button", { name: "重试读取配置" }));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "策略来源" })).toBeEnabled(),
  );
  await user.click(screen.getByRole("combobox", { name: "策略来源" }));
  await user.click(screen.getByRole("option", { name: "库策略" }));
  await user.click(screen.getByRole("button", { name: "创建策略" }));

  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
  expect(api.createBatch).toHaveBeenCalledExactlyOnceWith("work", {
    type: "library",
    id: "lib1",
    name: "库策略",
  });
});

it("配置加载失败后重试可恢复到可用表单", async () => {
  const user = userEvent.setup();
  vi.mocked(api.listPrompts).mockRejectedValueOnce(new Error("服务重启"));
  render(
    <NewStrategyDialog wid="work" title="素材" onClose={vi.fn()} onCreated={vi.fn()} />,
  );

  await user.click(await screen.findByRole("button", { name: "重试读取配置" }));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "策略来源" })).toBeEnabled(),
  );
});

it("只在指定目录创建策略，不启动跑批", async () => {
  const user = userEvent.setup();
  const onCreated = vi.fn();
  render(
    <NewStrategyDialog
      wid="work"
      title="素材"
      onClose={vi.fn()}
      onCreated={onCreated}
    />,
  );
  await user.type(screen.getByRole("textbox", { name: "策略名" }), "新策略");
  await user.click(screen.getByRole("button", { name: "创建策略" }));

  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
  expect(api.createBatch).toHaveBeenCalledExactlyOnceWith("work", {
    type: "scratch",
    name: "新策略",
    endpoint: "model",
    prompt: "caption",
    skills: [],
  });
  expect(api.startRun).not.toHaveBeenCalled();
});

it("创建失败保留输入，重试成功只通知一次", async () => {
  const user = userEvent.setup();
  const onCreated = vi.fn();
  vi.mocked(api.createBatch).mockRejectedValueOnce(new Error("引用已变化"));
  render(
    <NewStrategyDialog
      wid="work"
      title="素材"
      onClose={vi.fn()}
      onCreated={onCreated}
    />,
  );
  await user.type(screen.getByRole("textbox", { name: "策略名" }), "新策略");
  await user.click(screen.getByRole("button", { name: "创建策略" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("引用已变化");
  expect(screen.getByRole("textbox", { name: "策略名" })).toHaveValue("新策略");
  await user.click(screen.getByRole("button", { name: "创建策略" }));

  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
  expect(api.createBatch).toHaveBeenCalledTimes(2);
});
