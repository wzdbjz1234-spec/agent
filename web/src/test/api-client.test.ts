import { describe, expect, it, vi } from "vitest";

import { ApiClientError, api } from "../api/client";

describe("统一 API client", () => {
  it("把稳定错误 DTO 转成用户可理解的错误", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "资源不存在" } }), { status: 404, headers: { "Content-Type": "application/json" } })));
    await expect(api.getProject("missing")).rejects.toEqual(new ApiClientError(404, "NOT_FOUND", "资源不存在"));
    vi.unstubAllGlobals();
  });

  it("上传使用二进制请求和受控文件名 header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ file_version_id: "v1" }), { status: 201, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["a,b\n1,2"], "data.csv", { type: "text/csv" });
    await api.uploadFile("p1", file);
    expect(fetchMock).toHaveBeenCalledWith("/projects/p1/files", expect.objectContaining({ method: "POST", headers: expect.objectContaining({ "X-File-Name": "data.csv" }) }));
    vi.unstubAllGlobals();
  });

  it("把成功但非 JSON 的代理或缓存响应转成稳定错误", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<!doctype html>", { status: 200, headers: { "Content-Type": "text/html" } })));
    await expect(api.getProject("p1")).rejects.toEqual(new ApiClientError(200, "INVALID_RESPONSE", "服务返回了无法识别的数据，请刷新后重试"));
    vi.unstubAllGlobals();
  });
});
