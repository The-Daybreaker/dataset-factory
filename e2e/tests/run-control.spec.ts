import { expect, test } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

test("停止保留产物，选择重试可覆盖旧产物，运行中隐藏需确认", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(60_000);
  const destination = testInfo.outputPath("workdir");
  await mkdir(destination, { recursive: true });
  await writeFile(
    path.join(destination, "sample.png"),
    Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
      "base64",
    ),
  );
  await request.post("/fake-llm/__test__/gated-reset");
  const registration = await request.post("/api/workdirs", {
    data: { path: destination, title: "停止验证" },
  });
  expect(registration.status()).toBe(202);
  const accepted = await registration.json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/tasks/${accepted.task_id}`)).json())
          .status,
    )
    .toBe("succeeded");
  const endpointName = `gated-${testInfo.repeatEachIndex}`;
  const endpoint = await request.post("/api/endpoints", {
    data: {
      name: endpointName,
      base_url: "http://127.0.0.1:8765/fake-llm/v1",
      model: "gated-e2e-model",
      api_key: "sk-e2e-not-a-real-key", // pragma: allowlist secret
    },
  });
  expect(endpoint.ok()).toBeTruthy();
  const prompts = await (await request.get("/api/prompts")).json();
  const batch = await request.post(
    `/api/workdirs/${accepted.workdir.id}/batches`,
    {
      data: {
        type: "scratch",
        name: "停止验证",
        endpoint: endpointName,
        prompt: prompts[0].name,
        skills: [],
      },
    },
  );
  expect(batch.ok()).toBeTruthy();

  try {
    await page.goto("/");
    await page.getByRole("button", { name: "打标", exact: true }).click();
    await page.getByRole("button", { name: "选择工作目录与批次" }).click();
    await page.getByRole("menuitem", { name: /停止验证/ }).click();
    await page.getByRole("button", { name: "开始打标", exact: true }).click();
    const progress = page.getByRole("progressbar", { name: "运行进度" });
    await expect(progress).toHaveAttribute("aria-valuemax", "1");
    await expect
      .poll(
        async () =>
          (
            await (
              await request.post("/fake-llm/__test__/gated-entered")
            ).json()
          ).entered,
      )
      .toBe(true);
    await expect(progress).toHaveAttribute("aria-valuenow", "0");
    const stopResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/runs/stop") &&
        response.request().method() === "POST",
    );
    await page.getByTestId("run-stop").click();
    expect((await stopResponse).ok()).toBeTruthy();
    expect(
      (await request.post("/fake-llm/__test__/gated-release")).ok(),
    ).toBeTruthy();
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(
                `/api/workdirs/${accepted.workdir.id}/batches/s1/runs/latest`,
              )
            ).json()
          ).record?.status,
      )
      .toBe("interrupted");
    const latest = await (
      await request.get(
        `/api/workdirs/${accepted.workdir.id}/batches/s1/runs/latest`,
      )
    ).json();
    expect(latest.record.counters.succeeded).toBe(1);
    expect(
      await readFile(path.join(destination, "s1__sample.txt"), "utf8"),
    ).toContain("E2E 假模型的打标结果");
    await expect(progress).toBeHidden();

    await writeFile(path.join(destination, "s1__sample.txt"), "Old caption");
    await page.getByRole("button", { name: "刷新条目", exact: true }).click();
    const materials = page.getByRole("complementary", { name: "素材条目" });
    await materials.getByRole("button", { name: "选择", exact: true }).click();
    await materials.getByRole("button", { name: "已完成全选" }).click();
    await expect(materials.getByRole("checkbox", { name: "选择 sample.png" })).toBeChecked();
    await materials.getByRole("button", { name: "清空", exact: true }).click();
    await expect(materials.getByRole("checkbox", { name: "选择 sample.png" })).not.toBeChecked();
    await materials.getByRole("button", { name: "已完成全选" }).click();
    await materials.getByRole("button", { name: "加入重试", exact: true }).click();
    const retry = materials.getByRole("region", { name: "重试列表", exact: true });
    await expect(retry.getByText("sample.png", { exact: true })).toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "打标", exact: true }).click();
    await expect(retry.getByText("sample.png", { exact: true })).toBeVisible();

    await request.post("/fake-llm/__test__/gated-reset");
    // 从「重试列表」组头发车（名单非空时组头与顶栏各有一个「开始重试」，这里走组头那条，
    // 顶栏那条由 RunControl 组件测试覆盖）——名单已在界面上，本步顺带验证组头入口真的通。
    await retry.getByRole("button", { name: "开始重试", exact: true }).click();
    await expect.poll(async () => (await (await request.post("/fake-llm/__test__/gated-entered")).json()).entered).toBe(true);
    await expect(progress).toBeVisible();
    await page.getByRole("button", { name: "选择工作目录与批次" }).click();
    await page.getByRole("button", { name: "工作目录设置 停止验证", exact: true }).click();
    const settings = page.getByRole("region", { name: "工作目录设置", exact: true });
    await expect(settings.getByText("运行中", { exact: true })).toBeVisible();
    await expect(settings.getByRole("button", { name: "删除策略 s1" })).toBeDisabled();
    await settings.getByRole("button", { name: "隐藏", exact: true }).click();
    const confirmation = page.getByRole("dialog", { name: "隐藏策略？", exact: true });
    await expect(confirmation).toContainText("已完成条目与产物保留");
    await confirmation.getByRole("button", { name: "取消", exact: true }).click();
    const batchesUrl = `/api/workdirs/${accepted.workdir.id}/batches`;
    expect((await (await request.get(batchesUrl)).json())[0].active).toBe(true);
    await settings.getByRole("button", { name: "隐藏", exact: true }).click();
    await confirmation.getByRole("button", { name: "确认", exact: true }).click();
    await expect(confirmation).toHaveCount(0);
    expect((await (await request.get(batchesUrl)).json())[0].active).toBe(false);
    await request.post("/fake-llm/__test__/gated-release");
    await expect.poll(async () => {
      const result = await (await request.get(`${batchesUrl}/s1/runs/latest`)).json();
      return result.record?.run_id !== latest.record.run_id && result.record?.status === "interrupted";
    }).toBe(true);
    expect(await readFile(path.join(destination, "s1__sample.txt"), "utf8")).toBe("E2E 假模型的打标结果");
    await settings.getByRole("button", { name: "显示", exact: true }).click();
    await page.getByRole("dialog", { name: "显示策略？", exact: true }).getByRole("button", { name: "确认", exact: true }).click();
    await settings.getByRole("button", { name: "返回打标页", exact: true }).click();
    await expect(retry.getByText("sample.png", { exact: true })).toHaveCount(0);
    await expect(materials.getByRole("region", { name: "已完成", exact: true }).getByText("sample.png", { exact: true })).toBeVisible();
  } finally {
    await request.post("/fake-llm/__test__/gated-release");
  }
});
