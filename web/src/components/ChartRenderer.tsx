import { Alert, Button, Card, Descriptions, Image, Segmented, Space, Table, Typography } from "antd";
import { useEffect, useState } from "react";
import { VegaEmbed } from "react-vega";
import type { VisualizationSpec } from "vega-embed";

import { api, readArtifact } from "../api/client";
import type { Artifact, VegaLiteSpec } from "../api/types";

type ChartMode = "chart" | "table" | "explain";

function isSafeVegaLite(spec: VegaLiteSpec): boolean {
  const forbidden = new Set(["url", "href", "html", "iframe", "javascript", "signal", "signals", "expr", "expression"]);
  const visit = (value: unknown): boolean => {
    if (Array.isArray(value)) return value.every(visit);
    if (!value || typeof value !== "object") {
      return typeof value !== "string" || !/(https?|file|javascript|data):/i.test(value);
    }
    return Object.entries(value).every(([key, item]) => !forbidden.has(key.toLowerCase()) && visit(item));
  };
  const data = spec.data;
  return Boolean(
    spec.$schema?.startsWith("https://vega.github.io/schema/vega-lite/") &&
      data && typeof data.dataset_id === "string" && typeof data.content_hash === "string" &&
      visit(spec)
  );
}

/**
 * 统一图表出口：只把 Host 已校验的声明式 Vega-Lite 规范交给 Vega，失败即静态回退。
 * 不接受 HTML、URL、脚本、内嵌 values 或任意模型生成组件；大文件也只读取有界 JSON。
 */
export function ChartRenderer({ projectId, artifact }: { projectId: string; artifact: Artifact }) {
  const [mode, setMode] = useState<ChartMode>("chart");
  const [spec, setSpec] = useState<VegaLiteSpec | null>(null);
  const [mediaType, setMediaType] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;
    setSpec(null);
    setError(null);
    void readArtifact(projectId, artifact.id).then(async ({ blob, mediaType: contentType }) => {
      if (disposed) return;
      setMediaType(contentType);
      if (blob.size > 256 * 1024) {
        setError("图表规范超过浏览器预览上限，已阻止加载大载荷。");
        return;
      }
      if (contentType.includes("json") || artifact.name.toLowerCase().endsWith(".json")) {
        try {
          const value = JSON.parse(await blob.text()) as VegaLiteSpec;
          if (!isSafeVegaLite(value)) {
            setError("图表规范未通过浏览器二次安全检查，已回退静态资源。");
            return;
          }
          setSpec(value);
        } catch {
          setError("图表 JSON 无法解析，已回退静态资源。");
        }
      }
    }).catch(() => setError("图表暂时不可读取，稍后可重新打开。"));
    return () => { disposed = true; };
  }, [artifact.id, artifact.name, projectId]);

  const fallbackUrl = api.artifactContentUrl(projectId, artifact.id);
  const tableRows = spec?.data ? Object.entries(spec.data).map(([key, value]) => ({ key, value: String(value) })) : [];

  return (
    <Card size="small" title={artifact.name} extra={<Segmented value={mode} onChange={(value) => setMode(value as ChartMode)} options={[{ label: "图表", value: "chart" }, { label: "数据表", value: "table" }, { label: "说明", value: "explain" }]} />}>
      {error && <Alert type="warning" showIcon message={error} description="仅展示已发布的 PNG/SVG 或安全 JSON，不执行模型生成的脚本。" />}
      {mode === "chart" && spec && !error && <VegaEmbed spec={spec as VisualizationSpec} options={{ actions: false }} />}
      {mode === "chart" && !spec && !error && mediaType.includes("image") && <Image preview={false} src={fallbackUrl} alt="已发布静态图表" />}
      {mode === "chart" && error && <Image preview={false} src={fallbackUrl} alt="静态图表回退预览" />}
      {mode === "table" && (
        <Table size="small" pagination={false} rowKey="key" dataSource={tableRows} columns={[{ title: "字段", dataIndex: "key" }, { title: "值", dataIndex: "value" }]} locale={{ emptyText: "该产物只提供图表引用，未内嵌原始数据。" }} />
      )}
      {mode === "explain" && (
        <Space direction="vertical" size="small">
          <Typography.Text>图表来自 Host 已发布的 Artifact，规范绑定稳定 Dataset ID 与内容哈希。</Typography.Text>
          <Descriptions size="small" column={1} items={[{ key: "hash", label: "Artifact hash", children: artifact.content_hash }, { key: "source", label: "Dataset 引用", children: spec?.data ? `${String(spec.data.dataset_id)} / ${String(spec.data.content_hash)}` : "未在浏览器中展开" }]} />
        </Space>
      )}
      <Button type="link" size="small" href={fallbackUrl} target="_blank" rel="noreferrer">打开已发布资源</Button>
    </Card>
  );
}
