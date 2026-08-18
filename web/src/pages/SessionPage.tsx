import { ArrowLeftOutlined, ArrowRightOutlined, BarChartOutlined, FileSearchOutlined, SendOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Spin, Typography, message } from "antd";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useConversationMessages, useProject, useSessions } from "../api/hooks";
import { queryKeys } from "../api/query";
import type { ConversationMessage } from "../api/types";

const templates = [
  { label: "快速概览", icon: <FileSearchOutlined />, prompt: "请先列出当前项目文件，并指出可能的数据质量问题。" },
  { label: "寻找趋势", icon: <BarChartOutlined />, prompt: "从本地项目数据中寻找一个值得进一步验证的趋势。" }
];

export function SessionPage() {
  const { projectId = "", sessionId = "" } = useParams();
  const project = useProject(projectId);
  const sessions = useSessions(projectId);
  const messages = useConversationMessages(projectId, sessionId);
  const [prompt, setPrompt] = useState("");
  const [persist, setPersist] = useState(true);
  const [transientMessages, setTransientMessages] = useState<ConversationMessage[]>([]);
  const [analysisJobId, setAnalysisJobId] = useState<string | null>(null);
  const submitLock = useRef(false);
  const archived = project.data?.status === "ARCHIVED";
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const submit = useMutation({
    mutationFn: () => api.sendMessage(projectId, sessionId, prompt.trim(), persist),
    onSuccess: (result) => {
      setPrompt("");
      if (persist) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.messages(projectId, sessionId) });
      } else {
        setTransientMessages((current) => [...current, result.user, result.assistant]);
      }
      setAnalysisJobId(result.analysis_job?.task_id ?? null);
      if (result.snapshot_id) message.info("Agent 已固定本轮使用的数据版本");
    },
    onError: (error: Error) => message.error(error.message),
    onSettled: () => { submitLock.current = false; }
  });
  const startAnalysis = useMutation({
    mutationFn: async () => {
      const snapshot = await api.createSnapshot(projectId);
      return api.createTask(projectId, snapshot.id, prompt.trim(), sessionId);
    },
    onSuccess: (result) => navigate(`/tasks/${result.task.id}`),
    onError: (error: Error) => message.error(error.message)
  });
  const submitMessage = () => {
    if (submitLock.current || !prompt.trim() || archived) return;
    submitLock.current = true;
    submit.mutate();
  };
  const session = sessions.data?.find((item) => item.id === sessionId);

  if (project.isLoading || sessions.isLoading || messages.isLoading) return <Card><Spin /> 正在读取对话…</Card>;
  if (!project.data || !session) return <Card><Typography.Text type="danger">连续对话不存在，或不属于当前项目。</Typography.Text></Card>;
  const turns = [...(messages.data ?? []), ...transientMessages];
  return <>
    <div className="conversation-page">
      <header className="conversation-header"><Link className="icon-link" aria-label="返回项目" to={`/projects/${projectId}`}><ArrowLeftOutlined /></Link><div><Typography.Title level={2}>{session.label ?? "连续对话"}</Typography.Title><Typography.Text type="secondary">{project.data.name}</Typography.Text></div><span className="local-badge"><i />本地工作区 · Agent</span></header>
      {archived && <Alert type="warning" showIcon message="项目已归档" description="历史对话仍可读取，但不能继续发送新消息。" />}
      <div className="conversation-scroll">
        {!turns.length && <div className="conversation-empty"><span className="empty-mark">D</span><h2>直接和项目里的数据对话</h2><p>普通消息不会创建任务。Agent 会按需检索本地文件；需要 Python、SQL 或长时间计算时，再显式提交分析作业。</p><div className="prompt-suggestions">{templates.map((item) => <button type="button" key={item.label} onClick={() => setPrompt(item.prompt)}>{item.icon}<span><strong>{item.label}</strong><small>{item.prompt}</small></span><ArrowRightOutlined /></button>)}</div></div>}
        <div className="conversation-turns">{turns.map((item, index) => { if (item.role !== "user") return null; const answer = turns[index + 1]?.role === "assistant" ? turns[index + 1] : undefined; return <article className="conversation-turn" key={item.id}><div className="user-bubble">{item.content}</div><div className="assistant-row"><span className="assistant-mark">D</span><div><div className="turn-meta"><span>Data Agent</span><time>{new Date(answer?.created_at ?? item.created_at).toLocaleString()}</time></div><p>{answer?.content ?? "正在准备回答…"}</p></div></div></article>; })}</div>
        {submit.isPending && <div className="conversation-loading"><Spin /> Agent 正在检索本地数据…</div>}
      </div>
      <div className="composer-seat"><div className="composer-card"><textarea aria-label="问题" rows={3} value={prompt} disabled={archived || submit.isPending || startAnalysis.isPending} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && prompt.trim()) { event.preventDefault(); submitMessage(); } }} placeholder="向项目中的数据提问…" /><div className="composer-toolbar"><Checkbox checked={persist} onChange={(event) => setPersist(event.target.checked)}>保存对话</Checkbox><span>Enter 发送 · Shift + Enter 换行</span><Button type="default" loading={startAnalysis.isPending} disabled={archived || submit.isPending || !prompt.trim()} onClick={() => startAnalysis.mutate()}>隔离分析</Button><Button aria-label="发送消息" shape="circle" type="primary" icon={<SendOutlined />} loading={submit.isPending} disabled={archived || submit.isPending || startAnalysis.isPending || !prompt.trim()} onClick={submitMessage} /></div></div><p>普通消息不会创建任务；需要 Python、SQL、图表或长时间计算时，使用“隔离分析”创建一个显式 Analysis Job。</p></div>
      {analysisJobId && <div className="analysis-job-notice">已提交隔离分析作业 <Link to={`/tasks/${analysisJobId}`}>查看执行状态 <ArrowRightOutlined /></Link></div>}
    </div>
  </>;
}
