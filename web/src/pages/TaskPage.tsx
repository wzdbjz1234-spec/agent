import { ArrowLeftOutlined, CheckCircleOutlined, DatabaseOutlined, FileDoneOutlined, ReloadOutlined, StopOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Collapse, Empty, List, Space, Spin, Tabs, Timeline, Typography, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import { queryKeys } from "../api/query";
import { useProject, useTask, useTaskAnswer, useTaskEvents } from "../api/hooks";
import { ChartRenderer } from "../components/ChartRenderer";
import { StatusTag } from "../components/StatusTag";
import type { CreateTaskResponse, Task } from "../api/types";

function errorText(error: unknown) { return error instanceof Error ? error.message : "操作失败，请稍后重试。"; }

export function TaskPage() {
  const { taskId = "" } = useParams();
  const task = useTask(taskId);
  const project = useProject(task.data?.project_id);
  const answer = useTaskAnswer(taskId, Boolean(task.data));
  const events = useTaskEvents(taskId, task.data?.status);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: queryKeys.task(taskId) }); void queryClient.invalidateQueries({ queryKey: queryKeys.answer(taskId) }); };
  const action = useMutation<Task | CreateTaskResponse, Error, "cancel" | "resume" | "retry">({
    mutationFn: (kind: "cancel" | "resume" | "retry") => kind === "cancel" ? api.cancelTask(taskId) : kind === "resume" ? api.resumeTask(taskId) : api.retryTask(taskId),
    onSuccess: (result) => {
      refresh();
      // 终态重试会保留原 Task 的审计事实并创建新的 Task/Run，页面应随之切换到新执行。
      if ("task" in result) navigate(`/tasks/${result.task.id}`);
    },
    onError: (error) => message.error(errorText(error))
  });

  if (task.isLoading) return <Card><Spin /> 正在恢复 Task 事实状态…</Card>;
  if (task.isError || !task.data) return <Card><Typography.Text type="danger">Task 不存在或暂时不可读取：{task.error instanceof Error ? task.error.message : "请稍后重试"}</Typography.Text></Card>;
  const current = task.data;
  const terminal = ["COMPLETED", "FAILED", "CANCELLED"].includes(current.status);
  const answerData = answer.data;
  const resultPanel = <div className="result-stack">
    <section className="answer-summary"><div className="answer-kicker"><FileDoneOutlined /> 结构化回答</div>{answer.isLoading && <div className="result-loading"><Spin /> 正在整理结果…</div>}{!answer.isLoading && answerData?.answer && <div className="assistant-answer">{answerData.answer}</div>}{!answer.isLoading && answerData?.findings.length ? <div className="findings-list">{answerData.findings.map((finding) => <article key={finding.id}><div><StatusTag value={finding.status} /><Typography.Text>{finding.candidate.summary}</Typography.Text></div><div className="evidence-row">{finding.candidate.evidence.map((evidence) => <Typography.Text key={`${evidence.kind}-${evidence.target_id}`} code>{evidence.kind}:{evidence.target_id}</Typography.Text>)}</div></article>)}</div> : !answer.isLoading && !answerData?.answer && <div className="pending-answer"><h2>{terminal ? "当前没有结构化 Finding" : "分析正在进行"}</h2><p>{terminal ? "你仍可查看执行过程、数据集与披露信息。" : "完成后，结论与证据会出现在这里。"}</p></div>}{answerData?.disclosures.length ? <div className="disclosure"><strong>披露</strong>{answerData.disclosures.join("；")}</div> : null}</section>
    {(answerData?.artifacts.length ?? 0) > 0 && <section className="result-section"><div className="section-heading compact"><div><h2>可视化产物</h2><span>{answerData?.artifacts.length} 个 Artifact</span></div></div>{answerData?.artifacts.map((artifact) => <div key={artifact.id} className="artifact-frame"><ChartRenderer projectId={current.project_id} artifact={artifact} /></div>)}</section>}
  </div>;
  const processPanel = <section className="tab-surface"><Timeline className="event-timeline" items={events.map((event) => ({ key: event.id, color: event.event_type.includes("FAILED") ? "red" : event.event_type.includes("COMPLETED") || event.event_type.includes("SUCCEEDED") ? "green" : "blue", children: <Collapse ghost items={[{ key: String(event.id), label: <div className="event-title"><strong>{event.event_type}</strong><time>{new Date(event.occurred_at).toLocaleString()}</time></div>, children: Object.keys(event.payload).length > 0 ? <Typography.Text type="secondary">安全摘要：{Object.entries(event.payload).map(([key, value]) => `${key}=${String(value)}`).join(" · ")}</Typography.Text> : <Typography.Text type="secondary">没有额外的公开事件信息</Typography.Text> }]} /> }))} />{events.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无执行事件" />}</section>;
  const dataPanel = <section className="tab-surface"><List dataSource={answerData?.datasets ?? []} locale={{ emptyText: "没有正式 Dataset" }} renderItem={(dataset) => <List.Item><List.Item.Meta avatar={<span className="data-icon"><DatabaseOutlined /></span>} title={dataset.name} description={<Typography.Text code>{dataset.id} · {dataset.content_hash}</Typography.Text>} /></List.Item>} /></section>;
  const lineagePanel = <section className="tab-surface">{answerData?.lineage.length ? <List size="small" dataSource={answerData.lineage} renderItem={(item) => <List.Item><Typography.Text code>{item.source.kind}:{item.source.resource_id}</Typography.Text><span className="lineage-arrow">→</span><Typography.Text code>{item.target.kind}:{item.target.resource_id}</Typography.Text></List.Item>} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有可展示的血缘" />}</section>;
  return <>
    <div className="context-back">{project.data && <Link to={`/projects/${project.data.id}`}><ArrowLeftOutlined /> {project.data.name}</Link>}<span>/</span><span>任务结果</span></div>
    <div className="page-title task-title"><div><div className="eyebrow">分析任务</div><Typography.Title level={1}>Task 结果</Typography.Title><Space><StatusTag value={current.status} /><Typography.Text type="secondary">{current.id}</Typography.Text></Space></div><Space>{current.status === "WAITING" && <Button type="primary" icon={<CheckCircleOutlined />} onClick={() => action.mutate("resume")} loading={action.isPending}>恢复</Button>}{!terminal && current.status !== "WAITING" && <Button danger icon={<StopOutlined />} onClick={() => action.mutate("cancel")} loading={action.isPending}>取消</Button>}{terminal && current.status !== "CANCELLED" && <Button icon={<ReloadOutlined />} onClick={() => action.mutate("retry")} loading={action.isPending}>重试</Button>}</Space></div>
    {current.status === "WAITING" && <Alert type="warning" showIcon message={`任务正在等待：${current.wait_reason ?? "需要用户操作"}`} description="可以恢复继续；浏览器不会显示或要求输入隐藏模型消息。" />}
    {current.status === "FAILED" && <Alert type="error" showIcon message="任务执行失败" description="请检查本地诊断、模型配置或 Worker 日志；页面只显示稳定错误状态。" />}
    <Tabs className="task-tabs" defaultActiveKey="result" items={[{ key: "result", label: "结果", children: resultPanel }, { key: "process", label: `执行过程 ${events.length}`, children: processPanel }, { key: "data", label: `数据集 ${answerData?.datasets.length ?? 0}`, children: dataPanel }, { key: "lineage", label: `血缘 ${answerData?.lineage.length ?? 0}`, children: lineagePanel }]} />
  </>;
}
