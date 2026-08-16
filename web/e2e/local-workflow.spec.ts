import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("真实 FastAPI 上完成项目、文件、Snapshot、Session、Task 和取消流程", async ({ page }) => {
  const projectName = `浏览器验收-${Date.now()}`;
  await page.goto("/projects");
  await expect(page.getByRole("heading", { name: "从一个项目开始" })).toBeVisible();
  await page.getByRole("button", { name: "新建项目" }).click();
  await page.getByPlaceholder("例如：销售数据分析").fill(projectName);
  await page.getByRole("button", { name: "创 建", exact: true }).click();
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();

  await page.getByRole("tab", { name: /文件/ }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "sales.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("month,total\n2026-01,10\n2026-02,12\n")
  });
  await expect(page.getByText("sales.csv")).toBeVisible();
  await page.getByRole("button", { name: "创建 Snapshot" }).click();
  await expect(page.getByText("Snapshot 已固定")).toBeVisible();

  await page.getByRole("button", { name: "新建对话" }).click();
  await page.getByPlaceholder("例如：月度销售复盘").fill("浏览器验收对话");
  await page.getByRole("button", { name: "创 建", exact: true }).click();
  await expect(page.getByRole("heading", { name: "浏览器验收对话" })).toBeVisible();
  await page.getByRole("textbox", { name: "问题", exact: true }).fill("请概览当前文件。");
  await page.getByRole("button", { name: "提交问题" }).click();
  await expect(page.getByRole("heading", { name: "Task 结果" })).toBeVisible();
  await expect(page.getByText(/QUEUED|ACTIVE|WAITING|CANCELLED/).first()).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();
  await expect(page.locator(".ant-tag").filter({ hasText: "CANCELLED" }).first()).toBeVisible({ timeout: 10_000 });
});

test("WAITING 恢复和已校验 ChartArtifact 的 UI 状态", async ({ page }) => {
  let resumed = false;
  const waitingTask = {
    id: "e2e-waiting",
    project_id: "p-e2e",
    session_id: "s-e2e",
    prompt_ref: "task:e2e-waiting:state:PROMPT.json",
    prompt_hash: "prompt-hash",
    status: "WAITING",
    wait_reason: "USER_INPUT",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    completed_at: null
  };
  await page.route("**/tasks/e2e-waiting", async (route) => {
    if (route.request().resourceType() === "document") {
      await route.continue();
      return;
    }
    await route.fulfill({ json: { ...waitingTask, status: resumed ? "ACTIVE" : "WAITING", wait_reason: resumed ? null : "USER_INPUT" } });
  });
  await page.route("**/tasks/e2e-waiting/resume", async (route) => {
    resumed = true;
    await route.fulfill({ json: { ...waitingTask, status: "ACTIVE", wait_reason: null } });
  });
  await page.route("**/tasks/e2e-waiting/events*", async (route) => route.fulfill({ json: [] }));
  await page.route("**/tasks/e2e-waiting/events/stream*", async (route) => route.fulfill({ contentType: "text/event-stream", body: "" }));
  await page.route("**/projects/p-e2e", async (route) => route.fulfill({ json: { id: "p-e2e", name: "Mock evidence project", status: "ACTIVE", created_at: "2026-01-01T00:00:00Z", archived_at: null } }));
  await page.route("**/tasks/e2e-waiting/answer", async (route) => route.fulfill({ json: {
    task_id: "e2e-waiting", task_status: resumed ? "ACTIVE" : "WAITING", run_ids: ["r-e2e"], findings: [], datasets: [{ id: "d-e2e", project_id: "p-e2e", name: "sales", content_hash: "dataset-hash", task_id: "e2e-waiting", run_id: "r-e2e", created_at: "2026-01-01T00:00:00Z" }], artifacts: [{ id: "a-e2e", project_id: "p-e2e", name: "chart.json", content_hash: "artifact-hash", task_id: "e2e-waiting", run_id: "r-e2e", created_at: "2026-01-01T00:00:00Z" }], lineage: [], disclosures: []
  } }));
  await page.route("**/projects/p-e2e/artifacts/a-e2e/content", async (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ $schema: "https://vega.github.io/schema/vega-lite/v5.json", data: { dataset_id: "d-e2e", content_hash: "dataset-hash" }, mark: "bar", encoding: { x: { field: "month", type: "nominal" }, y: { field: "total", type: "quantitative" } } }) }));

  await page.goto("/tasks/e2e-waiting");
  await expect(page.getByText("USER_INPUT")).toBeVisible();
  await expect(page.getByText("chart.json")).toBeVisible();
  await expect(page.getByText("说明")).toBeVisible();
  await page.getByRole("button", { name: "恢复" }).click();
  await expect(page.locator(".ant-tag").filter({ hasText: "ACTIVE" }).first()).toBeVisible();
});

test("项目入口通过关键可访问性扫描", async ({ page }) => {
  await page.goto("/projects");
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical");
  expect(serious).toEqual([]);
});
