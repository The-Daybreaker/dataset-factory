import { expect, test } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

test("停止后保留当前素材产物并以中断状态收尾", async ({
  page,
  request,
}, testInfo) => {
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
      api_key: "sk-e2e-not-a-real-key",
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
  } finally {
    await request.post("/fake-llm/__test__/gated-release");
  }
});
