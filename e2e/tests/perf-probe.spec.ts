import { expect, test } from "@playwright/test";

import { stubApi } from "./fixtures/api-stubs";

// 性能探针（goal G4「减少无谓开销」的计量件）。
//
// 测的是能在浏览器里客观计量的一件事：3000 条清单下，搜索框连打 10 个字符会在主线程上留下
// 多少个长任务（>50ms）——「每次输入都全表小写化 + 全表重建」这类无谓开销就体现在这里。
// 数字只打印、不设门槛（设了就成了在慢机器上随机变红的门），但配两条硬断言兜住测量有效性：
// 输入前清单确实在 DOM 里、输入后确实被过滤空——缺了这两条，「长任务 0 个」可能只是页面本来就
// 是空的，量到的 0 说明不了任何性能结论。
//
// 条目放 `done` 分组：概览页的「排队中」计数取的是跑批队列而非 items 的 pending 分组，只有
// 已完成 / 未完成两类会渲染成条目按钮（探针停在概览页，不预先点进分组）。
//
// 数据与视觉基线共用同一份桩表，只把清单端点换成合成条目，保证两边跑的是同一份输入。

const TOTAL = 3000;
const ITEMS_KEY = "GET /api/workdirs/probe/batches/s1/items";
/**
 * 「量到了东西」这条前提的等待预算：默认 5s 在全新构建的预览服务 + 机器负载高时不够——
 * 首轮要把 3000 个按钮建出来，实测同一份 dist 单跑必过、跟在整条门禁后面偶发超时（2026-09-19
 * 连续两次 in-suite 失败 / standalone 两次通过，失败点都在这一条之前）。放宽到 30s 只影响
 * 「等多久算准备就绪」，不影响断言本身；用例总时限 180s 仍远在其上。
 */
const SEED_TIMEOUT = 30_000;

function doneItems(count: number) {
  const out = [];
  for (let index = 0; index < count; index += 1) {
    const seq = String(index).padStart(4, "0");
    out.push({
      item: `item-${seq}`,
      name: `sample-${seq}.png`,
      status: "done",
      media: "image",
      can_retry: false,
      in_retry: false,
    });
  }
  return out;
}

test("3000 条清单下搜索输入的长任务数", async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  await stubApi(page, [], {
    [ITEMS_KEY]: {
      batch: 1,
      query: "",
      groups: {
        done: doneItems(TOTAL),
        pending: [],
        failed: [],
        excluded: [],
        missing_asset: [],
        missing_product: [],
      },
    },
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "打标", exact: true }).click();
  const search = page.getByRole("textbox", { name: "搜索素材" });
  await expect(search).toBeVisible({ timeout: SEED_TIMEOUT });
  const listItems = page.getByRole("button", { name: /^sample-/ });
  await expect(listItems.first()).toBeVisible({ timeout: SEED_TIMEOUT });
  const domBefore = await listItems.count();

  const seen = await page.evaluateHandle(() => {
    const durations: number[] = [];
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) durations.push(entry.duration);
    });
    observer.observe({ entryTypes: ["longtask"] });
    return durations;
  });

  await search.click();
  await search.pressSequentially("abcdefghij", { delay: 30 });
  await page.waitForTimeout(500);
  const stats = await seen.evaluate((list: number[]) => ({
    count: list.length,
    total: Math.round(list.reduce((sum, value) => sum + value, 0)),
  }));
  const domAfter = await listItems.count();
  console.log(
    `LONGTASKS ${JSON.stringify(stats)} domBefore=${String(domBefore)} domAfter=${String(domAfter)}`,
  );
  await page.screenshot({ path: testInfo.outputPath("perf-search.png") });
  expect(domBefore).toBeGreaterThan(0);
  expect(domAfter).toBe(0);
});
