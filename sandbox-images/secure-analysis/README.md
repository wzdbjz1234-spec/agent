# secure-analysis image

镜像仅用于经 `OpenSandboxProvider` 认证的、不可信 Python/SQL/Skill Step。运行期必须以
锁定 digest、断网、非特权 `sandbox` 用户、只读根文件系统启动，并且只允许 `/project`
（只读）及当前 Task 的 `/task/working`、`/task/staging`（可写）三处挂载。

在具备 Docker、SBOM 和漏洞扫描工具的隔离构建环境执行：

```powershell
./build.ps1 -BaseImage 'python:3.12-slim@sha256:<digest>' -Tag 'dataharness/secure-analysis:build'
```

将实际 `image-digest.txt`、SBOM 与扫描结果保存在 `build-evidence/`。该目录被 `.gitignore`
忽略，因为证据需要绑定每一次实际构建；源码仓库绝不填写未验证的 digest 或扫描结论。
