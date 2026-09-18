import type { components } from "../../api-types.gen";

export type ItemRow = components["schemas"]["ItemRowView"];
export type ItemMap = ReadonlyMap<string, ItemRow>;

/** 未导入文件按完整文件名区分，不能覆盖同主干的在册素材。 */
export function itemKey(row: ItemRow): string {
  return row.status === "unimported" ? `unimported/${row.name}` : row.item;
}

export interface ItemUpdate {
  batch: number;
  item: string;
  status: "started" | "succeeded" | "failed";
  attempt: number;
  can_retry: boolean;
  reason_code: string | null;
  message: string | null;
}

export function parseItemUpdate(value: unknown): ItemUpdate {
  if (
    !value ||
    typeof value !== "object" ||
    !("batch" in value) ||
    typeof value.batch !== "number" ||
    !Number.isInteger(value.batch) ||
    !("item" in value) ||
    typeof value.item !== "string" ||
    !("status" in value) ||
    (value.status !== "started" &&
      value.status !== "succeeded" &&
      value.status !== "failed") ||
    !("attempt" in value) ||
    typeof value.attempt !== "number" ||
    !Number.isInteger(value.attempt) ||
    !("can_retry" in value) ||
    typeof value.can_retry !== "boolean" ||
    !("reason_code" in value) ||
    (value.reason_code !== null && typeof value.reason_code !== "string") ||
    !("message" in value) ||
    (value.message !== null && typeof value.message !== "string")
  )
    throw new Error("运行事件格式异常");
  return {
    batch: value.batch,
    item: value.item,
    status: value.status,
    attempt: value.attempt,
    can_retry: value.can_retry,
    reason_code: value.reason_code,
    message: value.message,
  };
}

/**
 * 把一条运行事件叠加到条目上。
 *
 * `can_retry` 直接取服务端下发的值——可重试与否是后端按原因码清单现判的结论，
 * 前端再抄一份清单就等于给同一件事实留了第二个来源（后端加码时会滞后到下一次
 * 全量刷新才纠正）。服务端与条目视图 `can_retry` 同一口径，两边不会各说各话。
 */
export function withItemUpdate(items: ItemMap, event: ItemUpdate): ItemMap {
  const row = items.get(event.item);
  if (!row) return items;
  const next = new Map(items);
  next.set(event.item, {
    ...row,
    status:
      event.status === "started"
        ? "queued"
        : event.status === "succeeded"
          ? "done"
          : "failed",
    attempt: event.attempt,
    reason_code: event.reason_code,
    message: event.message,
    can_retry: event.can_retry,
  });
  return next;
}

export const ITEM_GROUPS = [
  ["queued", "排队中"],
  ["done", "已完成"],
  ["failed", "未完成"],
  ["retry", "重试列表"],
  ["missing", "缺失"],
  ["unimported", "未导入"],
] as const;

export function itemsFromGroups(groups: Record<string, ItemRow[]>): ItemMap {
  const items = new Map<string, ItemRow>();
  for (const [group, rows] of Object.entries(groups)) {
    if (group === "retry") continue;
    for (const row of rows) items.set(itemKey(row), row);
  }
  for (const row of groups.retry ?? []) {
    items.set(row.item, { ...(items.get(row.item) ?? row), in_retry: true });
  }
  return items;
}

/** 未变化的行保留对象身份，避免重试标记更新使整列重新渲染。 */
export function withRetryItems(items: ItemMap, retry: readonly string[]): ItemMap {
  const wanted = new Set(retry);
  const next = new Map(items);
  for (const [key, row] of items) {
    if (row.status === "unimported") continue;
    if (row.in_retry !== wanted.has(key)) {
      next.set(key, { ...row, in_retry: wanted.has(key) });
    }
  }
  return next;
}

export function groupedItems(items: ItemMap, query: string): Record<string, ItemRow[]> {
  const groups: Record<string, ItemRow[]> = Object.fromEntries(
    ITEM_GROUPS.map(([key]) => [key, []]),
  );
  const needle = query.toLocaleLowerCase();
  for (const row of items.values()) {
    if (!row.name.toLocaleLowerCase().includes(needle)) continue;
    groups[row.status]?.push(row);
    if (row.in_retry) groups.retry?.push(row);
  }
  return groups;
}
