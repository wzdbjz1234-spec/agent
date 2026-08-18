/** 集中维护 Query Key，确保页面刷新、上传和任务终态后的失效范围一致。 */
export const queryKeys = {
  projects: ["projects"] as const,
  project: (id: string) => ["project", id] as const,
  files: (id: string) => ["project", id, "files"] as const,
  fileVersions: (projectId: string, fileId: string) => ["project", projectId, "file", fileId, "versions"] as const,
  sessions: (id: string) => ["project", id, "sessions"] as const,
  messages: (projectId: string, sessionId: string) => ["project", projectId, "session", sessionId, "messages"] as const,
  tasks: (projectId: string, sessionId?: string) => ["project", projectId, "tasks", sessionId ?? "all"] as const,
  task: (id: string) => ["task", id] as const,
  answer: (id: string) => ["task", id, "answer"] as const,
  findings: (id: string) => ["task", id, "findings"] as const,
  datasets: (id: string) => ["task", id, "datasets"] as const,
  artifacts: (id: string) => ["task", id, "artifacts"] as const,
  lineage: (id: string) => ["task", id, "lineage"] as const,
  diagnostics: ["diagnostics"] as const
};
