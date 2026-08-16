import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "./client";
import { queryKeys } from "./query";
import type { TaskEvent, TaskStatus } from "./types";

const terminalStatuses: TaskStatus[] = ["COMPLETED", "FAILED", "CANCELLED"];

export function useProjects() {
  return useQuery({ queryKey: queryKeys.projects, queryFn: api.listProjects });
}

export function useProject(projectId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.project(projectId ?? ""),
    queryFn: () => api.getProject(projectId as string),
    enabled: Boolean(projectId)
  });
}

export function useProjectFiles(projectId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.files(projectId ?? ""),
    queryFn: () => api.listFiles(projectId as string),
    enabled: Boolean(projectId)
  });
}

export function useFileVersions(projectId: string | undefined, fileId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.fileVersions(projectId ?? "", fileId ?? ""),
    queryFn: () => api.listFileVersions(projectId as string, fileId as string),
    enabled: Boolean(projectId && fileId && enabled)
  });
}

export function useSessions(projectId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.sessions(projectId ?? ""),
    queryFn: () => api.listSessions(projectId as string),
    enabled: Boolean(projectId)
  });
}

export function useProjectTasks(projectId: string | undefined, sessionId?: string) {
  return useQuery({
    queryKey: queryKeys.tasks(projectId ?? "", sessionId),
    queryFn: () => api.listTasks(projectId as string, sessionId),
    enabled: Boolean(projectId),
    staleTime: 1_000
  });
}

export function useTask(taskId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.task(taskId ?? ""),
    queryFn: () => api.getTask(taskId as string),
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && terminalStatuses.includes(status) ? false : 2_000;
    }
  });
}

export function useTaskAnswer(taskId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.answer(taskId ?? ""),
    queryFn: () => api.getTaskAnswer(taskId as string),
    enabled: Boolean(taskId && enabled),
    // Task 进入终态后回答及正式证据不会再变化，停止轮询以免历史页面持续产生请求。
    refetchInterval: (query) => {
      const status = query.state.data?.task_status;
      return enabled && (!status || !terminalStatuses.includes(status)) ? 2_000 : false;
    }
  });
}

/**
 * 通过 SSE 订阅简化事件，同时以 Runtime API 做事实恢复。
 *
 * EventSource 断线时不会假设内存中的游标仍然完整，而是携带最后一个事实事件 ID
 * 重新连接；页面刷新重新挂载后先从 /events 补齐，再打开 SSE。原始事件 payload
 * 只允许展示安全摘要，UI 不会把它解释为模型消息。
 */
export function useTaskEvents(taskId: string | undefined, status: TaskStatus | undefined) {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const cursorRef = useRef(0);
  const activeTaskIdRef = useRef<string | undefined>(undefined);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!taskId) {
      activeTaskIdRef.current = undefined;
      cursorRef.current = 0;
      setEvents([]);
      return;
    }
    let disposed = false;
    let source: EventSource | null = null;
    let reconnectTimer: number | undefined;
    let reconnectAttempts = 0;

    if (activeTaskIdRef.current !== taskId) {
      // React Router 复用 TaskPage 实例时必须清掉前一 Task 的游标；否则较大的旧事件
      // ID 会让新 Task 的历史和 SSE 都被误判为已消费。
      activeTaskIdRef.current = taskId;
      cursorRef.current = 0;
      setEvents([]);
    }

    const append = (event: TaskEvent) => {
      if (disposed || event.id <= cursorRef.current) return;
      cursorRef.current = event.id;
      setEvents((current) => [...current, event].slice(-100));
      void queryClient.invalidateQueries({ queryKey: queryKeys.task(taskId) });
    };

    // WAITING 不是执行中的实时阶段：先展示等待事实，用户恢复后状态变成 ACTIVE
    // 才重新建立 SSE，避免在等待用户操作期间无意义地占用长连接。
    const connect = () => {
      if (disposed || !status || terminalStatuses.includes(status) || status === "WAITING") return;
      source?.close();
      source = new EventSource(`/tasks/${encodeURIComponent(taskId)}/events/stream?after=${cursorRef.current}`);
      // 服务端用具体 event_type，客户端只订阅有限的生命周期/步骤事件；未知事件仍会
      // 留在 Runtime，下一次刷新通过 /events 补齐，不会因白名单遗漏而丢事实。
      const knownEventTypes = [
        "TASK_CREATED", "TASK_STARTED", "TASK_WAITING", "TASK_COMPLETED", "TASK_FAILED",
        "TASK_CANCELLED", "RUN_CREATED", "RUN_STARTED", "RUN_WAITING", "RUN_SUCCEEDED",
        "RUN_FAILED", "RUN_FAILURE_DIAGNOSIS", "RUN_CANCELLED", "AGENT_STARTED", "AGENT_WAITING", "AGENT_COMPLETED",
        "STEP_STARTED", "STEP_SUCCEEDED", "STEP_FAILED", "STEP_TIMED_OUT", "STEP_CANCELLED",
        "FINDING_VERIFIED", "FINDING_WARNING", "FINDING_REJECTED"
      ];
      knownEventTypes.forEach((eventType) => {
        const listener = (message: MessageEvent<string>) => {
          try {
            append(JSON.parse(message.data) as TaskEvent);
          } catch {
            // SSE 数据不符合稳定 DTO 时静默丢弃，事实仍可通过 API 重读。
          }
        };
        source?.addEventListener(eventType, listener);
      });
      source.onerror = () => {
        source?.close();
        if (!disposed) {
          // 服务器的流本身有生命周期上界；使用有上限的指数退避避免网络故障时每秒
          // 重试，同时仍允许长任务在恢复网络后从单调 cursor 补齐事实。
          const delay = Math.min(30_000, 1_000 * 2 ** reconnectAttempts);
          reconnectAttempts = Math.min(reconnectAttempts + 1, 5);
          reconnectTimer = window.setTimeout(connect, delay);
        }
      };
    };

    const restoreThenConnect = async () => {
      try {
        const items = await api.getTaskEvents(taskId, cursorRef.current);
        if (disposed) return;
        // REST 历史和 SSE 不再并行竞争。合并时游标只能前进，状态变化重挂载时也不会
        // 用较旧响应覆盖已显示事件。
        for (const item of items) append(item);
      } catch {
        // 历史读取失败不把内存当事实源；若当前状态允许，SSE 仍会从现有 cursor 尝试恢复。
      }
      connect();
    };
    void restoreThenConnect();
    return () => {
      disposed = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      source?.close();
    };
  }, [queryClient, status, taskId]);

  return events;
}

export function useInvalidateProject(projectId: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.files(projectId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.tasks(projectId) });
  };
}

export function useProjectMutations(projectId: string) {
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
    void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.files(projectId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.sessions(projectId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.tasks(projectId) });
  };
  return {
    upload: useMutation({ mutationFn: (file: File) => api.uploadFile(projectId, file), onSuccess: refresh }),
    snapshot: useMutation({ mutationFn: () => api.createSnapshot(projectId) }),
    session: useMutation({ mutationFn: (label: string) => api.createSession(projectId, label), onSuccess: refresh }),
    archive: useMutation({ mutationFn: () => api.archiveProject(projectId), onSuccess: refresh })
  };
}
