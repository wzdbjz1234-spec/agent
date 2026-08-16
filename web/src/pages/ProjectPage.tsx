import { ArrowRightOutlined, FileTextOutlined, InboxOutlined, MessageOutlined, MoreOutlined, PlusOutlined, SaveOutlined } from "@ant-design/icons";
import { Button, Card, Dropdown, Empty, Form, Input, List, Modal, Space, Tabs, Typography, Upload, message } from "antd";
import type { UploadProps } from "antd";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useFileVersions, useProject, useProjectFiles, useProjectMutations, useProjectTasks, useSessions } from "../api/hooks";
import { StatusTag } from "../components/StatusTag";
import type { ProjectFileView } from "../api/types";

function FileItem({ projectId, file }: { projectId: string; file: ProjectFileView }) {
  const [expanded, setExpanded] = useState(false);
  const versions = useFileVersions(projectId, file.file_id, expanded);
  return <div className="file-row"><span className="file-type-icon"><FileTextOutlined /></span><div className="file-meta"><Typography.Text strong className="file-name">{file.name}</Typography.Text><Typography.Text type="secondary">{file.media_type ?? "未知格式"} · {file.byte_size === null ? "大小未知" : `${Math.ceil(file.byte_size / 1024)} KB`} · {file.content_hash?.slice(0, 12) ?? "无 hash"}</Typography.Text>{expanded && <List className="version-list" size="small" loading={versions.isLoading} dataSource={versions.data ?? []} locale={{ emptyText: "没有历史版本" }} renderItem={(version) => <List.Item><Space><span>v{version.version_number}</span><StatusTag value={version.status} /><Typography.Text type="secondary">{new Date(version.created_at).toLocaleString()}</Typography.Text></Space></List.Item>} />}</div><Space><StatusTag value={file.status} /><Button type="text" onClick={() => setExpanded((value) => !value)}>{expanded ? "收起" : "版本"}</Button></Space></div>;
}

export function ProjectPage() {
  const { projectId = "" } = useParams();
  const project = useProject(projectId);
  const files = useProjectFiles(projectId);
  const sessions = useSessions(projectId);
  const tasks = useProjectTasks(projectId);
  const mutations = useProjectMutations(projectId);
  const [sessionOpen, setSessionOpen] = useState(false);
  const [sessionLabel, setSessionLabel] = useState("");
  const [snapshot, setSnapshot] = useState<{ id: string; entries: unknown[] } | null>(null);
  const navigate = useNavigate();
  const createSession = () => mutations.session.mutate(sessionLabel.trim() || "新对话", { onSuccess: (value) => { setSessionOpen(false); setSessionLabel(""); navigate(`/projects/${projectId}/sessions/${value.id}`); } });
  const uploadProps: UploadProps = { beforeUpload: (file) => { mutations.upload.mutate(file, { onSuccess: () => message.success(`已导入 ${file.name}`), onError: (error: Error) => message.error(error.message) }); return false; }, showUploadList: false, disabled: project.data?.status === "ARCHIVED" || mutations.upload.isPending };
  const createSnapshot = () => mutations.snapshot.mutate(undefined, { onSuccess: (value) => { setSnapshot(value); message.success(`Snapshot ${value.id.slice(0, 8)} 已创建`); }, onError: (error: Error) => message.error(error.message) });

  if (project.isLoading) return <Card loading />;
  if (project.isError || !project.data) return <Card><Typography.Text type="danger">项目不存在或暂时不可读取：{project.error instanceof Error ? project.error.message : "请稍后重试"}</Typography.Text></Card>;
  const archived = project.data.status === "ARCHIVED";
  const sessionItems = sessions.data ?? [];
  const taskItems = tasks.data ?? [];
  const fileItems = files.data ?? [];
  const tabs = [
    { key: "overview", label: "概览", children: <div className="project-overview">
      <section className="start-analysis-panel"><div><span className="eyebrow">下一步</span><h2>你想分析什么？</h2><p>{fileItems.length ? `当前项目已有 ${fileItems.length} 个文件。创建连续对话，开始提问并保留上下文。` : "先添加文件，再创建连续对话开始分析。"}</p></div><Button type="primary" size="large" icon={<MessageOutlined />} disabled={archived} onClick={() => setSessionOpen(true)}>开始新分析</Button></section>
      <div className="overview-columns"><section><div className="section-heading compact"><div><h2>连续对话</h2><span>保留上下文与追问</span></div></div>{sessions.isError ? <div className="inline-error">连续对话暂时不可读取，请稍后重试。</div> : sessionItems.length ? <div className="plain-list">{sessionItems.slice(0, 6).map((session) => <button type="button" key={session.id} onClick={() => navigate(`/projects/${projectId}/sessions/${session.id}`)}><span><strong>{session.label ?? "未命名对话"}</strong><small>{new Date(session.created_at).toLocaleString()}</small></span><ArrowRightOutlined /></button>)}</div> : <div className="mini-empty">还没有对话</div>}</section><section><div className="section-heading compact"><div><h2>最近任务</h2><span>分析运行记录</span></div></div>{tasks.isError ? <div className="inline-error">任务暂时不可读取，请稍后重试。</div> : taskItems.length ? <div className="plain-list">{taskItems.slice(0, 6).map((task) => <Link key={task.id} to={`/tasks/${task.id}`}><span><strong>分析 {task.id.slice(0, 8)}</strong><small>{new Date(task.created_at).toLocaleString()}</small></span><StatusTag value={task.status} /></Link>)}</div> : <div className="mini-empty">提交问题后，任务会出现在这里</div>}</section></div>
    </div> },
    { key: "files", label: `文件 ${fileItems.length}`, children: <section className="tab-surface"><div className="section-heading compact"><div><h2>项目文件</h2><span>新上传会创建不可变版本</span></div><Upload {...uploadProps}><Button icon={<InboxOutlined />} disabled={archived}>添加文件</Button></Upload></div><div className="file-list">{files.isLoading && <Typography.Text>正在读取文件…</Typography.Text>}{files.isError && <div className="inline-error">文件暂时不可读取，请稍后重试。</div>}{fileItems.map((file) => <FileItem key={file.file_version_id} projectId={projectId} file={file} />)}{!files.isLoading && !files.isError && !fileItems.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="支持 CSV、Parquet、XLSX、JSON、PDF、DOCX、PPTX、Markdown 和文本" />}</div></section> },
    { key: "tasks", label: `任务 ${taskItems.length}`, children: <section className="tab-surface"><List loading={tasks.isLoading} dataSource={taskItems} locale={{ emptyText: "该项目还没有任务" }} renderItem={(task) => <List.Item actions={[<Link key="open" to={`/tasks/${task.id}`}>查看 <ArrowRightOutlined /></Link>]}><List.Item.Meta title={<Space><Typography.Text>分析 {task.id.slice(0, 8)}</Typography.Text><StatusTag value={task.status} /></Space>} description={`${new Date(task.created_at).toLocaleString()}${task.wait_reason ? ` · 等待：${task.wait_reason}` : ""}`} /></List.Item>} /></section> }
  ];
  return <>
    <div className="context-back"><Link to="/projects">项目</Link><span>/</span><span>{project.data.name}</span></div>
    <div className="page-title project-title"><div><Typography.Title level={1}>{project.data.name}</Typography.Title><Space><StatusTag value={project.data.status} /><Typography.Text type="secondary">{project.data.id.slice(0, 12)}</Typography.Text></Space></div><Space><Button icon={<SaveOutlined />} disabled={archived || mutations.snapshot.isPending} onClick={createSnapshot}>创建 Snapshot</Button><Button icon={<PlusOutlined />} type="primary" disabled={archived} onClick={() => setSessionOpen(true)}>新建对话</Button><Dropdown menu={{ items: !archived ? [{ key: "archive", label: "归档项目", danger: true, onClick: () => Modal.confirm({ title: "归档项目？", content: "归档后仍保留历史事实，但不能再上传文件或创建新任务。", okText: "归档", cancelText: "取消", onOk: () => mutations.archive.mutate(undefined, { onSuccess: () => message.success("项目已归档") }) }) }] : [] }}><Button aria-label="更多操作" icon={<MoreOutlined />} /></Dropdown></Space></div>
    {snapshot && <div className="snapshot-notice"><SaveOutlined /><span><strong>Snapshot 已固定</strong> · {snapshot.entries.length} 个文件版本 · <Typography.Text code>{snapshot.id.slice(0, 12)}</Typography.Text></span></div>}
    <Tabs className="project-tabs" defaultActiveKey="overview" items={tabs} />
    <Modal title="新建连续对话" open={sessionOpen} okText="创建" cancelText="取消" confirmLoading={mutations.session.isPending} okButtonProps={{ disabled: !sessionLabel.trim() }} onCancel={() => setSessionOpen(false)} onOk={createSession}><Form layout="vertical"><Form.Item label="对话名称" required><Input autoFocus maxLength={255} value={sessionLabel} onChange={(event) => setSessionLabel(event.target.value)} placeholder="例如：月度销售复盘" /></Form.Item></Form></Modal>
  </>;
}
