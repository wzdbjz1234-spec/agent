import { ArrowRightOutlined, FolderOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Empty, Form, Input, Modal, Typography, message } from "antd";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { queryKeys } from "../api/query";
import { useProjects } from "../api/hooks";
import { StatusTag } from "../components/StatusTag";

export function ProjectsPage() {
  const { data: projects = [], isLoading, isError, refetch } = useProjects();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: () => api.createProject(name.trim()),
    onSuccess: (project) => { void queryClient.invalidateQueries({ queryKey: queryKeys.projects }); setOpen(false); setName(""); navigate(`/projects/${project.id}`); },
    onError: (error: Error) => message.error(error.message)
  });

  return <>
    <section className="projects-hero">
      <div className="eyebrow">本地数据分析工作台</div>
      <Typography.Title level={1}>从一个项目开始</Typography.Title>
      <Typography.Paragraph>汇集文件、连续分析与可核验证据。数据保留在本地工作区，任务在隔离环境中执行。</Typography.Paragraph>
      <Button size="large" type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>新建项目</Button>
    </section>
    <section className="section-block" aria-labelledby="project-list-title">
      <div className="section-heading"><div><h2 id="project-list-title">最近项目</h2><span>{projects.length ? `${projects.length} 个项目` : "你的分析空间"}</span></div><Button type="text" icon={<ReloadOutlined />} loading={isLoading} onClick={() => void refetch()}>刷新</Button></div>
      {isError && <div className="inline-error">项目列表暂时不可用，请确认本地服务已启动。</div>}
      {!isLoading && !isError && projects.length === 0 && <div className="empty-surface"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有项目"><Button onClick={() => setOpen(true)}>创建第一个项目</Button></Empty></div>}
      <div className="project-list">{projects.map((project) => <Link className="project-list-item" key={project.id} to={`/projects/${project.id}`}><span className="project-icon"><FolderOutlined /></span><span className="project-copy"><strong>{project.name}</strong><small>{new Date(project.created_at).toLocaleDateString()} 创建{project.archived_at ? ` · ${new Date(project.archived_at).toLocaleDateString()} 归档` : ""}</small></span><StatusTag value={project.status} /><ArrowRightOutlined className="row-arrow" /></Link>)}</div>
    </section>
    <Modal title="新建项目" open={open} okText="创建" cancelText="取消" confirmLoading={create.isPending} okButtonProps={{ disabled: !name.trim() }} onCancel={() => setOpen(false)} onOk={() => create.mutate()}><Form layout="vertical"><Form.Item label="项目名称" required><Input autoFocus maxLength={255} value={name} onChange={(event) => setName(event.target.value)} onPressEnter={() => name.trim() && create.mutate()} placeholder="例如：销售数据分析" /></Form.Item></Form></Modal>
  </>;
}
