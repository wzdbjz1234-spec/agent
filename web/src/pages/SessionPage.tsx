import { ArrowLeftOutlined, ArrowRightOutlined, BarChartOutlined, FileSearchOutlined, SendOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Spin, Typography, message } from "antd";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";

import { api } from "../api/client";
import { queryKeys } from "../api/query";
import { useProject, useProjectTasks, useSessions } from "../api/hooks";
import { StatusTag } from "../components/StatusTag";
import { useQueryClient } from "@tanstack/react-query";

const templates = [
  { label: "快速概览", icon: <FileSearchOutlined />, prompt: "请概览当前 Snapshot 中的文件，并指出需要注意的数据质量问题。" },
  { label: "趋势分析", icon: <BarChartOutlined />, prompt: "请从当前项目文件中寻找主要趋势，并给出可核验的证据引用。" }
];

export function SessionPage() {
  const { projectId = "", sessionId = "" } = useParams();
  const project = useProject(projectId);
  const sessions = useSessions(projectId);
  const tasks = useProjectTasks(projectId, sessionId);
  const [prompt, setPrompt] = useState("");
  const submitLock = useRef(false);
  const archived = project.data?.status === "ARCHIVED";
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const submit = useMutation({
    mutationFn: async () => {
      const snapshot = await api.createSnapshot(projectId);
      return api.createTask(projectId, snapshot.id, prompt.trim(), sessionId);
    },
    onSuccess: (result) => { void queryClient.invalidateQueries({ queryKey: queryKeys.tasks(projectId, sessionId) }); navigate(`/tasks/${result.task.id}`); },
    onError: (error: Error) => message.error(error.message),
    // React 的 disabled 状态要等下一次渲染才生效；用同步 ref 封住 Enter 与点击之间的
    // 极短窗口，确保一次用户意图只创建一组 Snapshot/Task。
    onSettled: () => { submitLock.current = false; }
  });
  const submitTask = () => {
    if (submitLock.current || !prompt.trim() || archived) return;
    submitLock.current = true;
    submit.mutate();
  };
  const session = sessions.data?.find((item) => item.id === sessionId);

  if (project.isLoading || sessions.isLoading) return <Card><Spin /> 正在读取对话…</Card>;
  if (!project.data || !session) return <Card><Typography.Text type="danger">连续对话不存在，或不属于当前项目。</Typography.Text></Card>;
  return <>
    <div className="conversation-page">
    <header className="conversation-header"><Link className="icon-link" aria-label="返回项目" to={`/projects/${projectId}`}><ArrowLeftOutlined /></Link><div><Typography.Title level={2}>{session.label ?? "连续对话"}</Typography.Title><Typography.Text type="secondary">{project.data.name}</Typography.Text></div><span className="local-badge"><i />本地工作区</span></header>
    {archived && <Alert type="warning" showIcon message="项目已归档" description="历史任务仍可查看，但不能继续创建问题。" />}
    <div className="conversation-scroll">
      {!tasks.isLoading && !tasks.data?.length && <div className="conversation-empty"><span className="empty-mark">D</span><h2>开始一次可靠的分析</h2><p>描述目标即可。每次提交都会固定当前文件版本，并保留可核验的结果。</p><div className="prompt-suggestions">{templates.map((item) => <button type="button" key={item.label} onClick={() => setPrompt(item.prompt)}>{item.icon}<span><strong>{item.label}</strong><small>{item.prompt}</small></span><ArrowRightOutlined /></button>)}</div></div>}
      {tasks.isLoading && <div className="conversation-loading"><Spin /> 正在读取历史任务…</div>}
      <div className="conversation-turns">{tasks.data?.map((task) => <article className="conversation-turn" key={task.id}><div className="user-bubble">分析任务 {task.id.slice(0, 8)}</div><div className="assistant-row"><span className="assistant-mark">D</span><div><div className="turn-meta"><StatusTag value={task.status} /><time>{new Date(task.created_at).toLocaleString()}</time></div><p>{task.wait_reason ? `任务正在等待：${task.wait_reason}` : task.status === "COMPLETED" ? "分析已完成，结构化结果与证据可以查看。" : task.status === "FAILED" ? "分析未能完成，请查看执行过程或重试。" : task.status === "CANCELLED" ? "任务已取消；已产生的事实记录仍然保留。" : "任务已提交，正在隔离环境中分析。"}</p><Link to={`/tasks/${task.id}`}>查看任务详情 <ArrowRightOutlined /></Link></div></div></article>)}</div>
    </div>
    <div className="composer-seat"><div className="composer-card"><textarea aria-label="问题" rows={3} value={prompt} disabled={archived || submit.isPending} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && prompt.trim()) { event.preventDefault(); submitTask(); } }} placeholder="向项目中的数据提问…" /><div className="composer-toolbar"><span>Enter 发送 · Shift + Enter 换行</span><Button aria-label="提交问题" shape="circle" type="primary" icon={<SendOutlined />} loading={submit.isPending} disabled={archived || !prompt.trim()} onClick={submitTask} /></div></div><p>仅展示事实状态与正式证据，不显示模型隐藏思考。</p></div>
    </div>
  </>;
}
