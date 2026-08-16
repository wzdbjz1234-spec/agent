import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ProjectsPage } from "../pages/ProjectsPage";

vi.mock("../api/hooks", () => ({ useProjects: () => ({ data: [{ id: "p1", name: "测试项目", status: "ACTIVE", created_at: "2026-01-01T00:00:00Z", archived_at: null }], isLoading: false, isError: false, refetch: vi.fn() }) }));

describe("项目页面", () => {
  it("显示项目和工作台入口", () => {
    const client = new QueryClient();
    render(<QueryClientProvider client={client}><MemoryRouter><ProjectsPage /></MemoryRouter></QueryClientProvider>);
    expect(screen.getByRole("heading", { name: "从一个项目开始" })).toBeInTheDocument();
    expect(screen.getByText("测试项目")).toBeInTheDocument();
  });
});
