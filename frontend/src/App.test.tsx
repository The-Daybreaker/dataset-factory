import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError } from "./api";

// App 渲染外壳并默认进提示词页（旧组件过渡期会拉列表与恢复会话）。这里把 API 层整体
// 换掉，让测试只关心「外壳与导航渲染得对不对」，不碰网络（页面级交互测试随后续页面重构补）。
// 部分 mock：只换 api 对象，ApiError / errorMessage 用真货（错误分档要靠真类的 kind 字段判）。
const apiMock = vi.hoisted(() => ({
  listStrategies: vi.fn(),
  listPrompts: vi.fn(),
  listSkills: vi.fn(),
  listEndpoints: vi.fn(),
  getService: vi.fn(),
  latestSession: vi.fn(),
  getConfig: vi.fn(),
}));

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  api: apiMock,
}));

/** 服务在线的默认应答（状态点的数据来源）；逐条用例改它之前都从这里起步。 */
const SERVICE_UP = {
  version: "v0.1.0",
  host: "127.0.0.1",
  port: 8000,
  started_at: "2026-09-19T00:00:00Z",
  log_file: "x",
};

describe("App 外壳", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listStrategies.mockResolvedValue([]);
    apiMock.listPrompts.mockResolvedValue([]);
    apiMock.listSkills.mockResolvedValue([]);
    apiMock.listEndpoints.mockResolvedValue([]);
    apiMock.getService.mockResolvedValue(SERVICE_UP);
    apiMock.latestSession.mockRejectedValue(new Error("还没有任何会话"));
    apiMock.getConfig.mockResolvedValue({
      name: null,
      base_url: null,
      model: null,
      api_key_configured: false,
      key_source: null,
    });
  });

  it("设置侧栏切换子页并返回进入前的工作页", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "设置" }));
    await user.click(screen.getByRole("button", { name: "技能" }));

    // 设置页现在是进页面才载的分包：先等它出现，再按原样断言（断言对象与强度不变）。
    // A9 按原型还原后设置页不再有页头 h1，子页标题与「端点配置 / 服务运行」同为 h2。
    await screen.findByRole("heading", { name: "技能", level: 2 });
    expect(screen.getByRole("heading", { name: "技能", level: 2 })).toBeVisible();
    // 「设置页不加二级导航地标」得是可证伪的断言：数当前有几个导航地标
    // （只有侧栏那一个），多加一个就会红——原来查一个根本不存在的名字必然永真。
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "返回工作区" }));
    expect(screen.getByRole("button", { name: "策略" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await user.click(screen.getByRole("button", { name: "切换提示词" }));
    expect(screen.getByRole("button", { name: "新建提示词" })).toBeVisible();
  });

  it("渲染品牌（副标题 + 版本脚注）与工作区两项导航", () => {
    render(<App />);

    expect(
      within(screen.getByTestId("sidebar")).getByText("Dataset Factory"),
    ).toBeInTheDocument();
    expect(screen.getByText("打标流水线工具")).toBeInTheDocument();
    expect(screen.getByText("v0.1.0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "策略" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "设置" })).toBeEnabled();
  });

  it("点击导航切换页面：设置页出现旧配置面板（过渡期嵌入）", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "设置" }));

    expect(screen.getByRole("button", { name: "设置" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("侧栏可折叠：收起后品牌文字隐藏、宽度收窄", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "收起侧栏" }));

    expect(
      within(screen.getByTestId("sidebar")).queryByText("Dataset Factory"),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "展开侧栏" }));
    expect(
      within(screen.getByTestId("sidebar")).getByText("Dataset Factory"),
    ).toBeInTheDocument();
  });

  it("关闭受理后状态点先转「正在停止」，逐秒重查探到不通即变红", async () => {
    render(<App />);
    expect(await screen.findByRole("img", { name: "服务运行中" })).toBeInTheDocument();

    apiMock.getService.mockRejectedValue(new ApiError("network", "连不上", null, null));
    window.dispatchEvent(new Event("df:service-stopping"));

    // 优雅停机要等手头请求做完，这段时间如实标「正在停止」；探到不通才定在红点上。
    await waitFor(
      () => expect(screen.getByRole("img", { name: "服务不可用" })).toBeInTheDocument(),
      { timeout: 3_000 },
    );
  });

  it("服务被脚本杀掉：一次失败请求就让状态点转红，报错不占界面位置", async () => {
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByRole("img", { name: "服务运行中" })).toBeInTheDocument();
    const down = new ApiError(
      "network",
      "无法连接后端服务——请确认 dsf serve 已启动、端口没有填错",
      null,
      null,
    );
    apiMock.getService.mockRejectedValue(down);
    apiMock.listStrategies.mockRejectedValue(down);

    await user.click(screen.getByRole("button", { name: "切换策略" }));

    // 浮层给一次性提醒（不铺红色提示条）。
    expect(await screen.findByText(/无法连接后端服务/)).toBeInTheDocument();
    // 关掉菜单再查点：Radix 弹层打开时会把页面其余部分标成 aria-hidden，按角色查不到。
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.getByRole("img", { name: "服务不可用" })).toBeInTheDocument(),
    );
    // 原来这里会出现一条带「刷新策略库」的常驻报错条——现在不占界面位置了。
    expect(
      screen.queryByRole("button", { name: "刷新策略库" }),
    ).not.toBeInTheDocument();
  });
});
