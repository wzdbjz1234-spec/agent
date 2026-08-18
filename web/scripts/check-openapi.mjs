import { readFile } from "node:fs/promises";

const openapi = JSON.parse(await readFile(new URL("../openapi.generated.json", import.meta.url), "utf8"));
const required = {
  "/projects": ["get", "post"],
  "/projects/{project_id}": ["get"],
  "/projects/{project_id}/archive": ["post"],
  "/projects/{project_id}/files": ["get", "post"],
  "/projects/{project_id}/snapshots": ["post"],
  "/projects/{project_id}/sessions": ["get", "post"],
  "/projects/{project_id}/sessions/{session_id}/messages": ["get", "post"],
  "/projects/{project_id}/tasks": ["get", "post"],
  "/tasks/{task_id}": ["get"],
  "/tasks/{task_id}/events/stream": ["get"],
  "/tasks/{task_id}/answer": ["get"],
  "/tasks/{task_id}/findings": ["get"],
  "/tasks/{task_id}/lineage": ["get"],
  "/diagnostics": ["get"],
  "/skills": ["get"]
};
for (const [path, methods] of Object.entries(required)) {
  for (const method of methods) {
    if (!openapi.paths?.[path]?.[method]) throw new Error(`OpenAPI 缺少 ${method.toUpperCase()} ${path}`);
  }
}
console.log(`OpenAPI 路由契约通过：${Object.keys(required).length} 个路径。`);
