# Sandbox Images

- V1 只维护锁定 digest 的 `secure-analysis` 镜像，供 OpenSandbox 使用。
- 预装 Python、DuckDB、pandas、PyArrow、Pandera、绘图库及审核过的 Skill 依赖。
- 默认断网、非特权用户、只读根文件系统；仅挂载当前 ProjectSnapshot 的只读资源与 Task working/staging。
- 禁止运行时 `pip/conda/apt` 安装、Docker socket、Host 凭据和 Runtime/Privacy DB。
- 构建产物必须保留 SBOM、依赖锁、漏洞扫描结果；启动后由 Provider 校验实际配置。
