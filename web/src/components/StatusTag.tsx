import { Tag } from "antd";

import type { FileVersionStatus, FindingStatus, ProjectStatus, TaskStatus } from "../api/types";

const colors: Record<string, string> = {
  ACTIVE: "blue",
  READY: "green",
  COMPLETED: "green",
  VERIFIED: "green",
  WARNING: "gold",
  WAITING: "orange",
  QUEUED: "processing",
  IMPORTING: "processing",
  FAILED: "red",
  REJECTED: "red",
  UNSUPPORTED: "volcano",
  CANCELLED: "default",
  ARCHIVED: "default"
};

export function StatusTag({ value }: { value: ProjectStatus | FileVersionStatus | TaskStatus | FindingStatus | string }) {
  return <Tag color={colors[value] ?? "default"}>{value}</Tag>;
}
