import { BugOutlined, FolderOpenOutlined, MenuFoldOutlined, MenuUnfoldOutlined } from "@ant-design/icons";
import { Button, Drawer, Space, Typography } from "antd";
import { useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { queryKeys } from "../api/query";

export function AppShell() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [diagnosticOpen, setDiagnosticOpen] = useState(false);
  const location = useLocation();
  const diagnostics = useQuery({ queryKey: queryKeys.diagnostics, queryFn: api.getDiagnostics, staleTime: 30_000 });

  // 侧栏只保留真正的全局入口；项目、会话、任务之间的返回路径由各页面上下文承担。
  const navigation = <>
    <div className="shell-brand">
      <Link to="/projects" aria-label="DataHarness 首页" onClick={() => setMobileOpen(false)}>
        <span className="brand-mark">D</span>{!collapsed && <span>DataHarness</span>}
      </Link>
      <Button className="sidebar-collapse" type="text" aria-label={collapsed ? "展开侧栏" : "收起侧栏"} icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed((value) => !value)} />
    </div>
    <nav className="shell-nav" aria-label="主导航">
      <Link className={location.pathname.startsWith("/projects") ? "active" : ""} to="/projects" onClick={() => setMobileOpen(false)}><FolderOpenOutlined /><span>项目</span></Link>
    </nav>
    <div className="shell-spacer" />
    <button className="diagnostic-trigger" type="button" onClick={() => setDiagnosticOpen(true)}><BugOutlined /><span>运行诊断</span><i className={diagnostics.data?.api === "ok" ? "health-dot healthy" : "health-dot"} /></button>
  </>;

  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="app-sidebar">{navigation}</aside>
      <header className="mobile-header"><Button type="text" aria-label="打开导航" icon={<MenuUnfoldOutlined />} onClick={() => setMobileOpen(true)} /><Link to="/projects"><span className="brand-mark">D</span>DataHarness</Link></header>
      <main className="app-content"><Outlet /></main>
      <Drawer className="mobile-navigation" placement="left" width={264} title={null} open={mobileOpen} onClose={() => setMobileOpen(false)}>{navigation}</Drawer>
      <Drawer title="本地诊断" open={diagnosticOpen} onClose={() => setDiagnosticOpen(false)}>
        {diagnostics.isLoading && <Typography.Text>正在读取诊断状态…</Typography.Text>}
        {diagnostics.data && <Space direction="vertical" size="middle" className="diagnostic-list">
          <Typography.Text>API：{String(diagnostics.data.api)}</Typography.Text>
          <Typography.Text>Worker：{String(diagnostics.data.worker)}</Typography.Text>
          <Typography.Text>模型配置：{diagnostics.data.model.configured ? "已配置" : "未配置"}</Typography.Text>
          <Typography.Text>Sandbox：{diagnostics.data.sandbox.configured ? "已锁定镜像" : "未锁定镜像"}</Typography.Text>
          <Typography.Text>数据目录：{String(diagnostics.data.paths?.runtime_data_root ?? "未提供")}</Typography.Text>
          <Typography.Text>剩余磁盘：{diagnostics.data.paths?.disk_free_bytes ? `${Math.round(Number(diagnostics.data.paths.disk_free_bytes) / 1024 / 1024)} MB` : "未知"}</Typography.Text>
        </Space>}
      </Drawer>
    </div>
  );
}
