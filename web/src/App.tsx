import { ConfigProvider, Result } from "antd";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { SessionPage } from "./pages/SessionPage";
import { TaskPage } from "./pages/TaskPage";

export function App() {
  // 以克制的中性色作为界面底色，仅在主操作和运行状态上使用蓝色，降低后台式组件的视觉噪声。
  return <ConfigProvider theme={{ token: { colorPrimary: "#315cec", colorInfo: "#315cec", colorText: "#20242c", colorTextSecondary: "#626975", colorTextDescription: "#777e89", colorBgLayout: "#f7f7f5", colorBorderSecondary: "#e7e7e3", borderRadius: 10, borderRadiusLG: 14, fontFamily: 'Inter, "Microsoft YaHei", sans-serif', controlHeight: 36 }, components: { Button: { fontWeight: 500, primaryShadow: "none" }, Card: { headerFontSize: 15 }, Modal: { borderRadiusLG: 18 } } }}><BrowserRouter><Routes><Route element={<AppShell />}><Route path="/" element={<Navigate to="/projects" replace />} /><Route path="/projects" element={<ProjectsPage />} /><Route path="/projects/:projectId" element={<ProjectPage />} /><Route path="/projects/:projectId/sessions/:sessionId" element={<SessionPage />} /><Route path="/tasks/:taskId" element={<TaskPage />} /><Route path="*" element={<Result status="404" title="页面不存在" extra={<Navigate to="/projects" replace />} />} /></Route></Routes></BrowserRouter></ConfigProvider>;
}
