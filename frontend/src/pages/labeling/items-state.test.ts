import { describe, expect, it } from "vitest";
import {
  groupedItems,
  type ItemRow,
  itemKey,
  itemsFromGroups,
  withItemUpdate,
  withRetryItems,
} from "./items-state";

const first: ItemRow = {
  item: "first",
  name: "first.jpg",
  media: "image",
  status: "done",
  can_retry: true,
  in_retry: false,
};
const second: ItemRow = { ...first, item: "second", name: "second.jpg" };

describe("素材 Map", () => {
  it("未导入同主干文件不覆盖在册素材，也不继承其重试标记", () => {
    const unsupported: ItemRow = {
      ...first,
      name: "first.psd",
      status: "unimported",
      can_retry: false,
    };
    const alternate: ItemRow = { ...unsupported, name: "first.tiff" };

    const items = withRetryItems(
      itemsFromGroups({ done: [first], unimported: [unsupported, alternate] }),
      ["first"],
    );

    expect(items.size).toBe(3);
    expect(items.get("first")?.status).toBe("done");
    expect(items.get(itemKey(unsupported))).toBe(unsupported);
    expect(groupedItems(items, "").unimported).toHaveLength(2);
    expect(groupedItems(items, "").retry).toHaveLength(1);
  });
  it("运行增量只替换目标行并使分组计数同步变化", () => {
    const items = itemsFromGroups({ done: [first, second] });
    const next = withItemUpdate(items, {
      batch: 1,
      item: "first",
      status: "failed",
      attempt: 4,
      reason_code: "timeout",
      message: "超时",
    });

    expect(next.get("second")).toBe(second);
    expect(groupedItems(next, "").failed).toHaveLength(1);
    expect(groupedItems(next, "").done).toHaveLength(1);
    expect(next.get("first")?.can_retry).toBe(true);
    expect(items.get("first")?.status).toBe("done");
  });

  it("普通分组与重试列表共享唯一条目，移出不丢失完成状态", () => {
    const items = itemsFromGroups({ done: [first, second], retry: [first] });

    const groups = groupedItems(items, "first");

    expect(items.size).toBe(2);
    expect(groups.done?.[0]).toBe(groups.retry?.[0]);
    expect(groupedItems(withRetryItems(items, []), "").retry).toEqual([]);
    expect(items.get("first")?.status).toBe("done");
  });

  it("改变一个重试标记只替换该行对象", () => {
    const items = itemsFromGroups({ done: [first, second] });

    const next = withRetryItems(items, ["first"]);

    expect(next.get("second")).toBe(items.get("second"));
    expect(next.get("first")).not.toBe(items.get("first"));
    expect(items.get("first")?.in_retry).toBe(false);
  });
});
