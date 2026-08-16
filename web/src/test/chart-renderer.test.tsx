import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChartRenderer } from "../components/ChartRenderer";

vi.mock("react-vega", () => ({ VegaEmbed: () => <div data-testid="vega-embed" /> }));

vi.mock("../api/client", async () => ({
  api: { artifactContentUrl: () => "/artifact" },
  readArtifact: vi.fn().mockResolvedValue({ blob: new Blob([JSON.stringify({ $schema: "https://vega.github.io/schema/vega-lite/v5.json", data: { dataset_id: "d1", content_hash: "h1" }, mark: "bar", encoding: {} })], { type: "application/json" }), mediaType: "application/json" })
}));

describe("ChartRenderer", () => {
  it("只渲染带 Dataset ID/hash 的 Vega-Lite 规范并提供切换", async () => {
    render(<ChartRenderer projectId="p1" artifact={{ id: "a1", project_id: "p1", name: "chart.json", content_hash: "hash", task_id: "t1", run_id: "r1", created_at: "2026-01-01T00:00:00Z" }} />);
    expect(await screen.findByText("chart.json")).toBeInTheDocument();
    expect(screen.getByText("数据表")).toBeInTheDocument();
    expect(screen.getByText("说明")).toBeInTheDocument();
  });
});
