import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

test("运行中的停止按钮调用停止接口", async ({ page, request }, testInfo) => {
  const source = testInfo.outputPath("source");
  const destination = testInfo.outputPath("workdir");
  await mkdir(source, { recursive: true });
  await mkdir(destination, { recursive: true });
  await writeFile(
    path.join(source, "sample.png"),
    Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64"),
  );
  const registration = await request.post("/api/workdirs", { data: { path: destination, title: "停止验证" } });
  expect(registration.status()).toBe(202);
  const accepted = await registration.json();
  await expect.poll(async () => (await (await request.get(`/api/tasks/${accepted.task_id}`)).json()).status).toBe("succeeded");
  const prompts = await (await request.get("/api/prompts")).json();
  const batch = await request.post(`/api/workdirs/${accepted.workdir.id}/batches`, {
    data: { type: "scratch", name: "停止验证", endpoint: "default", prompt: prompts[0].name, skills: [] },
  });
  expect(batch.ok()).toBeTruthy();

  await page.route("**/fake-llm/v1/chat/completions", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 20_000));
    await route.fulfill({ json: { detail: "cancelled" } });
  });
  const stopRequests: string[] = [];
  await page.route("**/api/workdirs/*/batches/s1/runs/stop", (route) => {
    stopRequests.push(route.request().url());
    return route.continue();
  });
  await page.goto("/");
  await page.getByRole("button", { name: "打标", exact: true }).click();
  await page.getByRole("button", { name: "选择工作目录与批次" }).click();
  await page.getByRole("menuitem", { name: /停止验证/ }).click();
  await page.getByRole("button", { name: "开始打标", exact: true }).click();
  const progress = page.getByRole("progressbar", { name: "运行进度" });
  await expect(progress).toHaveAttribute("aria-valuemax", "1");
  await page.getByTestId("run-stop").click();
  await expect.poll(() => stopRequests.length).toBe(1);
  expect(stopRequests[0]).toContain("/runs/stop");
});
