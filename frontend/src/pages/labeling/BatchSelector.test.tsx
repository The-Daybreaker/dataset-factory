import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BatchSelector, type WorkdirBatches } from "./BatchSelector";

const workdirs: WorkdirBatches[] = [
  {
    id: "first",
    title: "素材一",
    batches: [
      {
        id: "s1",
        seq: 1,
        name: "详细描述",
        description: "",
        active: true,
        created_at: "",
        product_count: 2,
      },
      {
        id: "s2",
        seq: 2,
        name: "隐藏策略",
        description: "",
        active: false,
        created_at: "",
        product_count: 0,
      },
    ],
  },
  {
    id: "second",
    title: "素材二",
    batches: [
      {
        id: "s1",
        seq: 1,
        name: "详细描述",
        description: "",
        active: true,
        created_at: "",
        product_count: 0,
      },
    ],
  },
];

describe("批次选择器", () => {
  it("下拉行内显示最近运行的状态章、进度与当前项勾", async () => {
    const user = userEvent.setup();
    const withRun: WorkdirBatches[] = [
      {
        id: "first",
        title: "素材一",
        batches: [
          {
            id: "s1",
            seq: 1,
            name: "详细描述",
            description: "",
            active: true,
            created_at: "",
            product_count: 2,
            run_status: "completed",
            run_done: 2,
            run_total: 3,
          },
          {
            id: "s2",
            seq: 2,
            name: "标签式短句",
            description: "",
            active: true,
            created_at: "",
            product_count: 0,
            run_status: null,
            run_done: null,
            run_total: null,
          },
        ],
      },
    ];
    render(
      <BatchSelector
        workdirs={withRun}
        value={{ workdirId: "first", batchId: "s1" }}
        onChange={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "选择工作目录与批次" }));

    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    const current = screen.getByRole("menuitem", { name: /详细描述/ });
    expect(current).toHaveAttribute("aria-current", "true");
    expect(current.querySelector("svg")).not.toBeNull();
    // 无运行记录的批次不带状态章与进度
    const other = screen.getByRole("menuitem", { name: /标签式短句/ });
    expect(within(other).queryByText(/\/ 3/)).not.toBeInTheDocument();
  });

  it("目录加号传递目录身份且关闭菜单", async () => {
    const user = userEvent.setup();
    const onNewStrategy = vi.fn();
    render(
      <BatchSelector
        workdirs={workdirs}
        value={null}
        onChange={vi.fn()}
        onNewStrategy={onNewStrategy}
      />,
    );
    await user.click(screen.getByRole("button", { name: "选择工作目录与批次" }));
    await user.click(screen.getByRole("button", { name: "新增策略 素材二" }));
    expect(onNewStrategy).toHaveBeenCalledExactlyOnceWith("second");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
  it("按目录分组且不展示停用批次", async () => {
    const user = userEvent.setup();
    render(<BatchSelector workdirs={workdirs} value={null} onChange={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "选择工作目录与批次" }));

    expect(screen.getByRole("group", { name: "素材一" })).toBeInTheDocument();
    expect(screen.getAllByRole("menuitem")).toHaveLength(2);
    expect(screen.queryByText("隐藏策略")).not.toBeInTheDocument();
  });

  it("同名同序号跨目录切换时返回目录与批次完整身份并关闭菜单", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <BatchSelector
        workdirs={workdirs}
        value={{ workdirId: "first", batchId: "s1" }}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "选择工作目录与批次" }));
    await user.click(
      within(screen.getByRole("group", { name: "素材二" })).getByRole("menuitem"),
    );

    expect(onChange).toHaveBeenCalledExactlyOnceWith({
      workdirId: "second",
      batchId: "s1",
    });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("空目录没有可触发的虚构批次", async () => {
    const user = userEvent.setup();
    render(
      <BatchSelector
        workdirs={[{ id: "empty", title: "空目录", batches: [] }]}
        value={null}
        onChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "选择工作目录与批次" }));

    expect(screen.getByText("没有启用的批次")).toBeInTheDocument();
    expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();
  });

  it("胶囊第三段按运行状态显示状态章，无记录时不显示", () => {
    const { rerender } = render(
      <BatchSelector
        workdirs={workdirs}
        value={{ workdirId: "first", batchId: "s1" }}
        onChange={vi.fn()}
        runState="completed"
      />,
    );

    expect(screen.getByText("已完成")).toBeInTheDocument();

    rerender(
      <BatchSelector
        workdirs={workdirs}
        value={{ workdirId: "first", batchId: "s1" }}
        onChange={vi.fn()}
        runState="running"
      />,
    );
    expect(screen.getByText("跑批中")).toBeInTheDocument();
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();

    rerender(
      <BatchSelector
        workdirs={workdirs}
        value={{ workdirId: "first", batchId: "s1" }}
        onChange={vi.fn()}
        runState={null}
      />,
    );
    expect(screen.queryByText("跑批中")).not.toBeInTheDocument();
  });
});
