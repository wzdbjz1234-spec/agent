import type {
  Artifact,
  ConversationMessage,
  ConversationResponse,
  CreateTaskResponse,
  Dataset,
  Diagnostics,
  Finding,
  Lineage,
  Project,
  ProjectFileVersion,
  ProjectFileView,
  ProjectSnapshot,
  Session,
  Task,
  TaskAnswer,
  TaskEvent
} from "./types";

/** API 请求错误只保留用户可理解的稳定 DTO，不把服务端异常正文直接显示到页面。 */
export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
  }
}

const jsonHeaders = { "Content-Type": "application/json" };
const requestTimeoutMs = 15_000;

async function fetchWithTimeout(path: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  let timedOut = false;
  const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, requestTimeoutMs);
  const abortFromCaller = () => controller.abort();
  init?.signal?.addEventListener("abort", abortFromCaller, { once: true });
  try {
    return await fetch(path, { ...init, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted) {
      throw new ApiClientError(
        0,
        timedOut ? "REQUEST_TIMEOUT" : "REQUEST_ABORTED",
        timedOut ? "请求超时，请确认本地服务状态后重试" : "请求已取消"
      );
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
    init?.signal?.removeEventListener("abort", abortFromCaller);
  }
}

function isJsonResponse(response: Response): boolean {
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  return contentType.includes("application/json") || contentType.includes("+json");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchWithTimeout(path, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers }
  });
  if (!response.ok) {
    let code = "HTTP_ERROR";
    let message = `请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as { error?: { code?: string; message?: string } };
      code = payload.error?.code ?? code;
      message = payload.error?.message ?? message;
    } catch {
      // 非 JSON 错误不读取正文，避免把 HTML/代理错误泄漏到 UI。
    }
    throw new ApiClientError(response.status, code, message);
  }
  if (response.status === 204) return undefined as T;
  // API 路由与 SPA 导航共享部分 URL。即使代理或缓存配置回归，也不能把 200 HTML
  // 当成 JSON 解析并把浏览器 SyntaxError 泄漏给用户。
  if (!isJsonResponse(response)) {
    throw new ApiClientError(response.status, "INVALID_RESPONSE", "服务返回了无法识别的数据，请刷新后重试");
  }
  return (await response.json()) as T;
}

export const api = {
  listProjects: () => request<Project[]>("/projects"),
  createProject: (name: string) =>
    request<Project>("/projects", { method: "POST", headers: jsonHeaders, body: JSON.stringify({ name }) }),
  archiveProject: (projectId: string) => request<Project>(`/projects/${projectId}/archive`, { method: "POST" }),
  getProject: (projectId: string) => request<Project>(`/projects/${projectId}`),
  listFiles: (projectId: string) => request<ProjectFileView[]>(`/projects/${projectId}/files`),
  listFileVersions: (projectId: string, fileId: string) =>
    request<ProjectFileVersion[]>(`/projects/${projectId}/files/${fileId}/versions`),
  uploadFile: async (projectId: string, file: File) => {
    // Response(file) 兼容浏览器 File 与测试环境的最小 File polyfill，仍保持二进制
    // body；文件名只进入受控 header，由 FastAPI 再做一次规范化和大小/格式校验。
    const body = await new Response(file).arrayBuffer();
    return request<ProjectFileVersion>(`/projects/${projectId}/files`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-File-Name": file.name },
      body
    });
  },
  createSnapshot: (projectId: string) =>
    request<ProjectSnapshot>(`/projects/${projectId}/snapshots`, { method: "POST" }),
  listSessions: (projectId: string) => request<Session[]>(`/projects/${projectId}/sessions`),
  createSession: (projectId: string, label?: string) =>
    request<Session>(`/projects/${projectId}/sessions`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ label: label || null })
    }),
  listConversationMessages: (projectId: string, sessionId: string) =>
    request<ConversationMessage[]>(`/projects/${projectId}/sessions/${sessionId}/messages`),
  sendMessage: (projectId: string, sessionId: string, content: string, persist = true) =>
    request<ConversationResponse>(`/projects/${projectId}/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ content, persist })
    }),
  listTasks: (projectId: string, sessionId?: string) =>
    request<Task[]>(`/projects/${projectId}/tasks${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`),
  createTask: (projectId: string, snapshotId: string, prompt: string, sessionId?: string) =>
    request<CreateTaskResponse>(`/projects/${projectId}/tasks`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ project_snapshot_id: snapshotId, prompt, session_id: sessionId ?? null })
    }),
  getTask: (taskId: string) => request<Task>(`/tasks/${taskId}`),
  cancelTask: (taskId: string) => request<Task>(`/tasks/${taskId}/cancel`, { method: "POST" }),
  resumeTask: (taskId: string) => request<Task>(`/tasks/${taskId}/resume`, { method: "POST" }),
  retryTask: (taskId: string, snapshotId?: string) =>
    request<CreateTaskResponse>(`/tasks/${taskId}/retry`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(snapshotId ? { project_snapshot_id: snapshotId } : {})
    }),
  getTaskEvents: (taskId: string, after = 0) =>
    request<TaskEvent[]>(`/tasks/${taskId}/events?after=${after}`),
  getTaskAnswer: (taskId: string) => request<TaskAnswer>(`/tasks/${taskId}/answer`),
  getTaskFindings: (taskId: string) => request<Finding[]>(`/tasks/${taskId}/findings`),
  getTaskDatasets: (taskId: string) => request<Dataset[]>(`/tasks/${taskId}/datasets`),
  getTaskArtifacts: (taskId: string) => request<Artifact[]>(`/tasks/${taskId}/artifacts`),
  getTaskLineage: (taskId: string) => request<Lineage[]>(`/tasks/${taskId}/lineage`),
  getDiagnostics: () => request<Diagnostics>("/diagnostics"),
  artifactContentUrl: (projectId: string, artifactId: string) =>
    `/projects/${projectId}/artifacts/${artifactId}/content`
};

export async function readArtifact(projectId: string, artifactId: string): Promise<{ blob: Blob; mediaType: string }> {
  const response = await fetchWithTimeout(api.artifactContentUrl(projectId, artifactId));
  if (!response.ok) throw new ApiClientError(response.status, "ARTIFACT_READ_FAILED", "图表或产物暂时不可读取");
  return { blob: await response.blob(), mediaType: response.headers.get("content-type") ?? "" };
}
