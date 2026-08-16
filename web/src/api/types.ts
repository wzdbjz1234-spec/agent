/**
 * API 边界的稳定 DTO。
 *
 * 这些类型只描述 FastAPI 对外返回的 JSON，不包含 Workspace 路径、模型消息、
 * 隐藏思考或 Runtime SQLite 行。它们与 `web/openapi.generated.json` 的路由契约
 * 一起校验，后端模型变更时先更新 DTO 和 API 检查，再修改页面。
 */
export type ProjectStatus = "ACTIVE" | "ARCHIVED";
export type FileVersionStatus = "IMPORTING" | "READY" | "FAILED" | "UNSUPPORTED";
export type TaskStatus = "QUEUED" | "ACTIVE" | "WAITING" | "COMPLETED" | "FAILED" | "CANCELLED";
export type WaitReason =
  | "USER_INPUT"
  | "BUDGET_EXHAUSTED"
  | "RETRY_APPROVAL"
  | "MISSING_DEPENDENCY";
export type FindingStatus = "DRAFT" | "VERIFIED" | "WARNING" | "REJECTED";
export type ResourceKind = "FILE_VERSION" | "STEP" | "DATASET" | "ARTIFACT" | "FINDING";

export interface Project {
  id: string;
  name: string;
  status: ProjectStatus;
  created_at: string;
  archived_at: string | null;
}

export interface ProjectFileView {
  project_id: string;
  file_id: string;
  file_version_id: string;
  name: string;
  status: FileVersionStatus;
  content_hash: string | null;
  media_type: string | null;
  byte_size: number | null;
}

export interface ProjectFileVersion extends ProjectFileView {
  version_number: number;
  created_at: string;
  finalized_at: string | null;
}

export interface SnapshotEntry {
  file_version_id: string;
  file_id: string;
  version_number: number;
  status: FileVersionStatus;
  content_hash: string | null;
}

export interface ProjectSnapshot {
  id: string;
  project_id: string;
  created_at: string;
  entries: SnapshotEntry[];
  index_version: string | null;
  dataset_version_ids: string[];
}

export interface Session {
  id: string;
  project_id: string | null;
  label: string | null;
  created_at: string;
}

export interface Task {
  id: string;
  project_id: string;
  session_id: string | null;
  prompt_ref: string | null;
  prompt_hash: string | null;
  status: TaskStatus;
  wait_reason: WaitReason | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface Run {
  id: string;
  task_id: string;
  project_id: string;
  project_snapshot_id: string;
  status: string;
  phase: string;
  wait_reason: WaitReason | null;
  cancel_requested_at: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface CreateTaskResponse {
  task: Task;
  run: Run;
}

export interface EvidenceRef {
  kind: "FILE" | "STEP" | "DATASET" | "ARTIFACT";
  target_id: string;
  content_hash: string;
  locator: string | null;
}

export interface FindingCandidate {
  task_id: string;
  run_id: string;
  project_snapshot_id: string;
  summary: string;
  evidence: EvidenceRef[];
  coverage_report_id: string | null;
  created_at: string;
}

export interface Finding {
  id: string;
  candidate: FindingCandidate;
  status: FindingStatus;
  verified_at: string | null;
}

export interface Dataset {
  id: string;
  project_id: string;
  name: string;
  content_hash: string;
  task_id: string | null;
  run_id: string | null;
  created_at: string;
}

export interface Artifact {
  id: string;
  project_id: string;
  name: string;
  content_hash: string;
  task_id: string | null;
  run_id: string | null;
  created_at: string;
}

export interface ResourceRef {
  kind: ResourceKind;
  resource_id: string;
  content_hash: string | null;
}

export interface Lineage {
  id: string;
  run_id: string;
  source: ResourceRef;
  target: ResourceRef;
  created_at: string;
}

export interface TaskAnswer {
  task_id: string;
  task_status: TaskStatus;
  answer: string | null;
  run_ids: string[];
  findings: Finding[];
  datasets: Dataset[];
  artifacts: Artifact[];
  lineage: Lineage[];
  disclosures: string[];
}

export interface TaskEvent {
  id: number;
  aggregate_type: string;
  aggregate_id: string;
  event_type: string;
  occurred_at: string;
  payload: Record<string, unknown>;
}

export interface ApiErrorPayload {
  error: { code: string; message: string; trace_id?: string | null };
}

export interface Diagnostics {
  api: string;
  worker: string;
  model: Record<string, unknown>;
  sandbox: Record<string, unknown>;
  paths?: Record<string, unknown>;
}

export type VegaLiteSpec = Record<string, unknown> & {
  $schema?: string;
  data?: Record<string, unknown>;
};
