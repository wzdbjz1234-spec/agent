<#
.SYNOPSIS
    DataHarness Phase 13 一次性本机发布预检与安装。

.DESCRIPTION
    该脚本只创建 .dataharness 派生状态和配置，不删除 runtime-data；会从本地配置复制
    [model].api_key，但不会把密钥写入部署状态、日志或诊断输出。它要求真实 Docker/uv/
    锁文件/镜像证据；无法确认安全前置条件时失败关闭。
#>

param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'dataharness.local.toml'),
    [string]$SandboxConfigPath = (Join-Path $env:USERPROFILE '.sandbox.toml'),
    [int]$ApiPort = 8000,
    [int]$SandboxPort = 18080,
    [string]$ImageTag = 'secure-analysis:1.0.0',
    [string]$BaseImage,
    [string]$SandboxServerVersion = '0.2.2',
    [switch]$SkipImageBuild,
    [switch]$SkipWebBuild,
    [switch]$AllowOccupiedPorts
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'scripts/deployment_common.ps1')

$root = (Resolve-Path $PSScriptRoot).Path
$paths = Get-DeploymentPaths -Root $root
Initialize-DeploymentDirectories -Paths $paths

function Invoke-CheckedNative {
    <# 执行外部命令并以退出码判断结果；不会把环境变量秘密作为参数传入。 #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage,
        [int]$TimeoutSeconds = 600
    )
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $FilePath
    $info.WorkingDirectory = $root
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    # 这里不重定向大体量构建输出，避免等待期间填满管道造成假死；日志中只保留
    # 失败的稳定诊断，子进程不会得到任何密钥参数。
    $info.RedirectStandardOutput = $false
    $info.RedirectStandardError = $false
    foreach ($argument in $Arguments) { [void]$info.ArgumentList.Add([string]$argument) }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) { throw "$FailureMessage；无法启动子进程。" }
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill($true) } catch { try { $process.Kill() } catch { } }
        throw "$FailureMessage；超过 ${TimeoutSeconds}s 超时，已停止受管子进程。"
    }
    if ($process.ExitCode -ne 0) {
        throw "$FailureMessage；退出码 $($process.ExitCode)。请查看命令诊断并按提示修复。"
    }
    return @()
}

function Set-ManagedImageDigest {
    <# 从用户配置生成独立 managed config，只注入实际镜像 digest，不覆盖用户文件。 #>
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath,
        [Parameter(Mandatory = $true)][string]$Digest
    )
    $source = Get-Content -LiteralPath $SourcePath -Raw -Encoding utf8
    # 本地配置允许 [model].api_key；目标文件位于 .dataharness（已被 .gitignore 忽略），
    # 但整个 setup 输出和部署状态仍不得打印或持久化密钥正文。
    $lines = [System.Collections.Generic.List[string]]::new()
    $inSandbox = $false
    $hasSandboxSection = $false
    $found = $false
    foreach ($line in ($source -split "`r?`n")) {
        if ($line -match '^\s*\[([^\]]+)\]\s*$') {
            if ($inSandbox -and -not $found) {
                $lines.Add("image_digest = `"$Digest`"")
                $found = $true
            }
            $inSandbox = ($Matches[1] -eq 'sandbox')
            if ($inSandbox) { $hasSandboxSection = $true }
        }
        if ($inSandbox -and $line -match '^\s*image_digest\s*=') {
            $lines.Add("image_digest = `"$Digest`"")
            $found = $true
        }
        else {
            $lines.Add($line)
        }
    }
    if (-not $hasSandboxSection) {
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -ne '') { $lines.Add('') }
        $lines.Add('[sandbox]')
        $lines.Add("endpoint = `"http://127.0.0.1:$SandboxPort`"")
        $lines.Add('runtime = "secure-analysis"')
        $lines.Add("image_digest = `"$Digest`"")
        $lines.Add('network_enabled = false')
        $found = $true
    }
    elseif ($inSandbox -and -not $found) {
        $lines.Add("image_digest = `"$Digest`"")
    }
    ($lines -join "`r`n") | Set-Content -LiteralPath $DestinationPath -Encoding utf8NoBOM
}

function Assert-SandboxAllowedHostPaths {
    <# OpenSandbox 的 server 侧白名单是第二道边界：只能允许 Project Workspace，
    # 不能用整个 runtime-data 抵消 mount_resolver 对 Runtime/Privacy DB 的隔离。 #>
    param(
        [Parameter(Mandatory = $true)][string]$ApplicationConfigPath,
        [Parameter(Mandatory = $true)][string]$ServerConfigPath
    )
    $applicationConfig = Get-Content -LiteralPath $ApplicationConfigPath -Raw -Encoding utf8
    $runtimeMatch = [regex]::Match($applicationConfig, '(?ms)^\s*\[paths\].*?^\s*runtime_data_root\s*=\s*["'']([^"'']+)["'']')
    if (-not $runtimeMatch.Success) { throw '配置缺少 [paths].runtime_data_root，无法验证 OpenSandbox 挂载白名单。' }
    $runtimeRoot = [string]$runtimeMatch.Groups[1].Value
    if (-not [IO.Path]::IsPathRooted($runtimeRoot)) {
        $runtimeRoot = Join-Path (Split-Path -Parent $ApplicationConfigPath) $runtimeRoot
    }
    $trimChars = [char[]]@('\', '/')
    $expected = [IO.Path]::GetFullPath((Join-Path $runtimeRoot 'projects')).TrimEnd($trimChars)
    $serverConfig = Get-Content -LiteralPath $ServerConfigPath -Raw -Encoding utf8
    $allowedMatch = [regex]::Match($serverConfig, '(?ms)^\s*allowed_host_paths\s*=\s*\[(.*?)\]')
    if (-not $allowedMatch.Success) {
        throw 'OpenSandbox 配置缺少 [storage].allowed_host_paths；必须仅允许 runtime-data/projects。'
    }
    $configured = @([regex]::Matches($allowedMatch.Groups[1].Value, '["'']([^"'']+)["'']') | ForEach-Object {
            [IO.Path]::GetFullPath($_.Groups[1].Value.Replace('/', '\')).TrimEnd($trimChars)
        })
    if ($configured.Count -ne 1 -or -not $configured[0].Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "OpenSandbox allowed_host_paths 必须且只能是 $expected；不要允许 runtime.db、privacy 或整个 runtime-data。"
    }
}

Write-Host 'DataHarness Phase 13 setup：开始本机预检。'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw '需要 PowerShell 7 或更高版本（pwsh.exe）；旧版无法安全传递受管进程参数和环境边界。'
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw '未找到 uv。请安装 uv 后重新执行 setup.ps1。不会使用 pip 回退。'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw '未找到 Docker CLI。请安装并启动 Docker Desktop 后重新执行 setup.ps1。'
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue) -and -not (Test-Path (Join-Path $root 'web/dist/index.html'))) {
    throw '没有 pnpm 且缺少 web/dist/index.html。请在开发机安装 Node.js/pnpm 构建 WebUI，发布机则携带预构建 web/dist。'
}

if (-not (Test-Path -LiteralPath (Join-Path $root 'uv.lock') -PathType Leaf)) {
    throw '缺少 uv.lock；拒绝在未锁定依赖的环境安装。'
}
if (-not (Test-Path -LiteralPath (Join-Path $root 'web/pnpm-lock.yaml') -PathType Leaf)) {
    throw '缺少 web/pnpm-lock.yaml；拒绝在未锁定依赖的环境构建 WebUI。'
}
if (-not (Test-Path -LiteralPath $SandboxConfigPath -PathType Leaf)) {
    throw "缺少 OpenSandbox 配置 $SandboxConfigPath；请创建仅绑定 127.0.0.1、Docker backend、dns+nft egress 和最小 allowed_host_paths 的配置。"
}
$sourceConfig = if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) { (Resolve-Path $ConfigPath).Path } else { Join-Path $root 'dataharness.example.toml' }
Assert-SandboxAllowedHostPaths -ApplicationConfigPath $sourceConfig -ServerConfigPath $SandboxConfigPath

if (-not $AllowOccupiedPorts) {
    foreach ($port in @($ApiPort, $SandboxPort)) {
        if (-not (Test-LocalPortAvailable -Port $port)) {
            throw "端口 $port 已被占用；请用 status.ps1 定位，或停止冲突进程后再 setup。"
        }
    }
}

[void](Invoke-CheckedNative -FilePath 'uv' -Arguments @('lock', '--check') -FailureMessage 'uv.lock 校验失败' -TimeoutSeconds 60)
[void](Invoke-CheckedNative -FilePath 'uv' -Arguments @('sync', '--locked') -FailureMessage 'uv 依赖安装失败' -TimeoutSeconds 900)
[void](Invoke-CheckedNative -FilePath 'docker' -Arguments @('info') -FailureMessage 'Docker Desktop/daemon 不可用' -TimeoutSeconds 45)

$webIndex = Join-Path $root 'web/dist/index.html'
if (-not $SkipWebBuild -and (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Push-Location (Join-Path $root 'web')
    try {
        [void](Invoke-CheckedNative -FilePath 'pnpm' -Arguments @('install', '--frozen-lockfile', '--ignore-scripts') -FailureMessage 'WebUI 锁文件安装失败' -TimeoutSeconds 900)
        [void](Invoke-CheckedNative -FilePath 'pnpm' -Arguments @('run', 'typecheck') -FailureMessage 'WebUI TypeScript 校验失败' -TimeoutSeconds 300)
        [void](Invoke-CheckedNative -FilePath 'pnpm' -Arguments @('run', 'lint') -FailureMessage 'WebUI lint 失败' -TimeoutSeconds 300)
        [void](Invoke-CheckedNative -FilePath 'pnpm' -Arguments @('run', 'openapi:check') -FailureMessage 'WebUI OpenAPI 契约校验失败' -TimeoutSeconds 120)
        [void](Invoke-CheckedNative -FilePath 'pnpm' -Arguments @('run', 'build') -FailureMessage 'WebUI 构建失败' -TimeoutSeconds 600)
    }
    finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath $webIndex -PathType Leaf)) {
    throw '缺少 web/dist/index.html；FastAPI 无法同源提供 WebUI，setup 失败关闭。'
}

$evidenceRoot = Join-Path $root 'sandbox-images/secure-analysis/build-evidence'
$digestPath = Join-Path $evidenceRoot 'image-digest.txt'
$needBuild = -not (Test-Path -LiteralPath $digestPath -PathType Leaf)
if ($needBuild -and $SkipImageBuild) {
    throw '缺少 secure-analysis 镜像 digest 证据，不能使用 -SkipImageBuild 绕过。'
}
if ($needBuild) {
    if ([string]::IsNullOrWhiteSpace($BaseImage)) {
        throw '缺少不可变 BaseImage。请传入 -BaseImage "python:3.12-slim@sha256:<64位小写digest>"；禁止使用 mutable tag。'
    }
    if ($BaseImage -notmatch '^[^@\s]+@sha256:[0-9a-f]{64}$') {
        throw 'BaseImage 必须是带 @sha256:<64位小写digest> 的镜像引用。'
    }
    $pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
    if (-not $pwsh) { throw '缺少 pwsh.exe；无法为镜像构建设置超时边界。' }
    [void](Invoke-CheckedNative -FilePath $pwsh -Arguments @('-NoProfile', '-File', (Join-Path $root 'sandbox-images/secure-analysis/build.ps1'), '-BaseImage', $BaseImage, '-Tag', $ImageTag) -FailureMessage 'secure-analysis 镜像构建失败' -TimeoutSeconds 1800)
}
if (-not (Test-Path -LiteralPath $digestPath -PathType Leaf)) {
    throw '缺少 image-digest.txt；拒绝把 tag 当作安全镜像身份。'
}
$digest = (Get-Content -LiteralPath $digestPath -Raw -Encoding ascii).Trim()
if ($digest -notmatch '^sha256:[0-9a-f]{64}$') { throw 'image-digest.txt 不是合法 sha256 digest。' }
$imageDigestLine = (& docker image inspect $ImageTag --format '{{index .RepoDigests 0}}' 2>&1)
if ($LASTEXITCODE -ne 0 -or [string]$imageDigestLine -notmatch "@${digest}$") {
    throw "本地镜像 $ImageTag 不存在或 digest 与证据不一致；请重新构建并重新生成 SBOM/漏洞扫描证据。"
}
foreach ($evidence in @('sbom.spdx.json', 'vuln-scan.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $evidenceRoot $evidence) -PathType Leaf)) {
        throw "缺少 $evidence；setup 不会伪造发布合规证据。请运行 Docker Scout/OSV 扫描。"
    }
}
[void](Invoke-CheckedNative -FilePath 'uv' -Arguments @('run', 'python', 'scripts/release_check.py', '--require-image') -FailureMessage '镜像/发布证据校验失败' -TimeoutSeconds 120)

Set-ManagedImageDigest -SourcePath $sourceConfig -DestinationPath $paths.Config -Digest $digest
[void](Invoke-CheckedNative -FilePath 'uv' -Arguments @('run', 'dataharness', 'check', '--config', $paths.Config) -FailureMessage 'DataHarness 配置校验失败' -TimeoutSeconds 120)
$keyConfigured = Test-ConfiguredModelApiKey -Path $paths.Config

$setupRecord = [ordered]@{
    schema_version = 1
    setup_at_utc = [DateTime]::UtcNow.ToString('o')
    config_path = $paths.Config
    sandbox_config_path = (Resolve-Path $SandboxConfigPath).Path
    sandbox_server_version = $SandboxServerVersion
    api_port = $ApiPort
    sandbox_port = $SandboxPort
    image_tag = $ImageTag
    image_digest = $digest
    model_api_key_configured = $keyConfigured
    web_dist_present = $true
    node_required_at_runtime = $false
    data_deletion_performed = $false
}
Write-DeploymentJson -Path $paths.SetupMarker -Value $setupRecord
Remove-Item -LiteralPath $paths.WorkerShutdown -Force -ErrorAction SilentlyContinue

Write-Host "setup 通过：WebUI 已构建，secure-analysis digest=$digest。"
if ($keyConfigured) { Write-Host "模型 API Key：已配置（仅状态，不显示值）。" }
else { Write-Warning '模型 API Key：未配置。请在 dataharness.local.toml 的 [model].api_key 填写后重新 setup；或用 -AllowMissingModelKey 启动 WAITING 模式。' }
Write-Host "状态目录：$($paths.State)；运行时不需要 Node.js。"
