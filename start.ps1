<#
.SYNOPSIS
    启动 DataHarness 的 OpenSandbox Server、API 和 Worker 三个独立宿主进程。

.DESCRIPTION
    只启动由 setup.ps1 生成的配置和已验证镜像。脚本按 Sandbox -> API -> Worker 顺序
    启动，并以回环 TCP/HTTP/心跳分别验证依赖。重复执行会复用已证明归属的 PID，不会复制
    Worker 或 OpenSandbox；模型密钥只从本地受管 TOML 配置读取，不进入参数、状态或日志。
#>

param(
    [int]$ApiPort = 0,
    [int]$SandboxPort = 0,
    [string]$SandboxConfigPath,
    [string]$SandboxServerVersion,
    [switch]$AllowMissingModelKey
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'scripts/deployment_common.ps1')

$root = (Resolve-Path $PSScriptRoot).Path
$paths = Get-DeploymentPaths -Root $root
$setup = Read-DeploymentJson -Path $paths.SetupMarker
if ($null -eq $setup) {
    throw '缺少 .dataharness/setup.json；请先运行 .\setup.ps1 完成预检和发布物验证。'
}
if (-not (Test-Path -LiteralPath ([string]$setup.config_path) -PathType Leaf)) {
    throw 'managed config 不存在；请重新运行 setup.ps1，不会删除 runtime-data。'
}
$ApiPort = if ($ApiPort -gt 0) { $ApiPort } else { [int]$setup.api_port }
$SandboxPort = if ($SandboxPort -gt 0) { $SandboxPort } else { [int]$setup.sandbox_port }
$SandboxConfigPath = if ($SandboxConfigPath) { $SandboxConfigPath } else { [string]$setup.sandbox_config_path }
$SandboxServerVersion = if ($SandboxServerVersion) { $SandboxServerVersion } else { [string]$setup.sandbox_server_version }

if (-not (Test-Path -LiteralPath $SandboxConfigPath -PathType Leaf)) {
    throw "OpenSandbox 配置不存在：$SandboxConfigPath；不会启动一个未核验的 Server。"
}
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw '缺少 .venv/Scripts/python.exe；请重新运行 setup.ps1 的 uv sync --locked。'
}
if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
    throw '未找到 uvx；请确认 uv 安装完整。不会用未知命令启动 OpenSandbox。'
}

$keyConfigured = Test-ConfiguredModelApiKey -Path ([string]$setup.config_path)
if (-not $keyConfigured -and -not $AllowMissingModelKey) {
    throw '模型 API Key 未配置；请在 dataharness.local.toml 的 [model].api_key 填写后重新运行 setup，或显式使用 -AllowMissingModelKey 启动 WAITING 模式。'
}

$manifest = Read-DeploymentJson -Path $paths.Manifest
$existing = @{}
if ($null -ne $manifest -and $null -ne $manifest.roles) {
    foreach ($role in @('sandbox', 'api', 'worker')) {
        $property = $manifest.roles.PSObject.Properties[$role]
        if ($null -ne $property) { $existing[$role] = $property.Value }
    }
}

function Get-ExistingOwnedRole {
    param([Parameter(Mandatory = $true)][string]$Role)
    if (-not $existing.ContainsKey($Role)) { return $null }
    $record = Normalize-ManagedProcessRecord -Value $existing[$Role]
    $snapshot = Get-ManagedProcessSnapshot -Record $record
    if ($snapshot.State -eq 'RUNNING' -and -not $snapshot.Owned) {
        throw "$Role 的记录指向一个仍在运行但无法证明归属的 PID；为防误杀，先人工定位 $($record.pid)，不会覆盖状态。"
    }
    if ($snapshot.State -eq 'RUNNING') { return $record }
    return $null
}

function Resolve-SandboxProcessRecord {
    <# uvx 可能在启动后退出并留下真正的 OpenSandbox 子进程；记录子进程 PID，
    # 使 status/stop 不会只管理已经结束的 uvx 包装器。 #>
    param(
        [Parameter(Mandatory = $true)]$Record,
        [Parameter(Mandatory = $true)][string]$ConfigPath
    )
    $configMarker = [IO.Path]::GetFullPath($ConfigPath)
    $candidates = @(Get-CimInstance Win32_Process | Where-Object {
            $line = [string]$_.CommandLine
            $line -and $line.Contains('opensandbox-server') -and $line.Contains($configMarker)
        })
    $preferred = @($candidates | Where-Object { [string]$_.Name -ieq 'opensandbox-server.exe' })
    if ($preferred.Count -eq 1) {
        $candidates = $preferred
    }
    elseif ($candidates.Count -ne 1) {
        throw "无法唯一定位 OpenSandbox Server 受管进程（候选数=$($candidates.Count)）；拒绝继续管理未知进程。"
    }
    $candidate = $candidates[0]
    $process = Get-Process -Id ([int]$candidate.ProcessId) -ErrorAction Stop
    [pscustomobject]@{
        role = 'sandbox'
        pid = [int]$candidate.ProcessId
        start_time_utc = $process.StartTime.ToUniversalTime().ToString('o')
        log = $Record.log
        executable = if ($candidate.ExecutablePath) { [string]$candidate.ExecutablePath } else { $Record.executable }
        arguments = @($Record.arguments)
        command_match = 'opensandbox-server'
    }
}

$sandboxRecord = Get-ExistingOwnedRole -Role 'sandbox'
$apiRecord = Get-ExistingOwnedRole -Role 'api'
$workerRecord = Get-ExistingOwnedRole -Role 'worker'
$started = [System.Collections.Generic.List[object]]::new()

try {
    if ($null -eq $sandboxRecord) {
        if (-not (Test-LocalPortAvailable -Port $SandboxPort)) {
            throw "OpenSandbox 端口 $SandboxPort 已被外部进程占用；拒绝把未知服务当作 Sandbox。"
        }
        $sandboxLog = Join-Path $paths.Logs 'sandbox.log'
        $sandboxRecord = Start-ManagedProcess `
            -Role 'sandbox' `
            -FilePath ((Get-Command uvx).Source) `
            -ArgumentList @('--from', "opensandbox-server==$SandboxServerVersion", 'opensandbox-server', '--config', $SandboxConfigPath) `
            -WorkingDirectory $root `
            -LogPath $sandboxLog `
            -CommandMatch 'opensandbox-server' `
            -EnvironmentOverrides @{ OPENSANDBOX_INSECURE_SERVER = 'YES' } `
            -DropSensitiveEnvironment
        $sandboxRecord = Normalize-ManagedProcessRecord -Value $sandboxRecord
        $started.Add($sandboxRecord)
    }
    if (-not (Wait-TcpEndpoint -HostName '127.0.0.1' -Port $SandboxPort -TimeoutSeconds 45)) {
        throw "OpenSandbox Server 未在 127.0.0.1:$SandboxPort 可达；请查看 $($paths.Logs)\sandbox.log、docker info 和 .sandbox.toml。"
    }
    if ($null -ne $sandboxRecord -and $sandboxRecord.executable -match '(?i)uvx') {
        $sandboxRecord = Resolve-SandboxProcessRecord -Record $sandboxRecord -ConfigPath $SandboxConfigPath
        $started.Add($sandboxRecord)
    }

    if ($null -eq $apiRecord) {
        if (-not (Test-LocalPortAvailable -Port $ApiPort)) {
            throw "API 端口 $ApiPort 已被外部进程占用；拒绝覆盖。"
        }
        $apiLog = Join-Path $paths.Logs 'api.log'
        $apiRecord = Start-ManagedProcess `
            -Role 'api' `
            -FilePath $python `
            -ArgumentList @('-m', 'dataharness', 'serve', '--config', [string]$setup.config_path, '--host', '127.0.0.1', '--port', [string]$ApiPort) `
            -WorkingDirectory $root `
            -LogPath $apiLog `
            -CommandMatch 'dataharness.*serve' `
            -EnvironmentOverrides @{ DATAHARNESS_WORKER_HEALTH_FILE = $paths.WorkerHealth }
        $apiRecord = Normalize-ManagedProcessRecord -Value $apiRecord
        $started.Add($apiRecord)
    }
    if (-not (Wait-HttpReady -Uri "http://127.0.0.1:$ApiPort/readyz" -TimeoutSeconds 30)) {
        throw "DataHarness API 未通过 /readyz；请查看 $($paths.Logs)\api.log。"
    }

    if ($null -eq $workerRecord) {
        Remove-Item -LiteralPath $paths.WorkerShutdown -Force -ErrorAction SilentlyContinue
        # 旧 Worker 退出后心跳文件可能仍是 STARTING/STOPPING；新进程必须从干净的
        # 派生状态开始，不能把旧心跳当成当前 Worker 已就绪。
        Remove-Item -LiteralPath $paths.WorkerHealth -Force -ErrorAction SilentlyContinue
        $workerLog = Join-Path $paths.Logs 'worker.log'
        $workerRecord = Start-ManagedProcess `
            -Role 'worker' `
            -FilePath $python `
            -ArgumentList @('-m', 'dataharness', 'worker', '--config', [string]$setup.config_path, '--owner', 'dataharness-managed-worker', '--health-file', $paths.WorkerHealth, '--shutdown-file', $paths.WorkerShutdown, '--heartbeat-seconds', '1') `
            -WorkingDirectory $root `
            -LogPath $workerLog `
            -CommandMatch 'dataharness.*worker'
        $workerRecord = Normalize-ManagedProcessRecord -Value $workerRecord
        $started.Add($workerRecord)
    }

    $workerDeadline = [DateTime]::UtcNow.AddSeconds(20)
    $workerHealth = $null
    while ([DateTime]::UtcNow -lt $workerDeadline) {
        $workerHealth = Read-DeploymentJson -Path $paths.WorkerHealth
        if ($null -ne $workerHealth -and [string]$workerHealth.status -in @('IDLE', 'RUNNING')) { break }
        Start-Sleep -Milliseconds 250
    }
    if ($null -eq $workerHealth -or [string]$workerHealth.status -notin @('IDLE', 'RUNNING')) {
        throw "Worker 未产生可用心跳；请查看 $($paths.Logs)\worker.log。"
    }

    $newManifest = [ordered]@{
        schema_version = 1
        started_at_utc = [DateTime]::UtcNow.ToString('o')
        config_path = [string]$setup.config_path
        api_port = $ApiPort
        sandbox_port = $SandboxPort
        roles = [ordered]@{
            sandbox = $sandboxRecord
            api = $apiRecord
            worker = $workerRecord
        }
        node_required_at_runtime = $false
        data_deletion_performed = $false
    }
    Write-DeploymentJson -Path $paths.Manifest -Value $newManifest
    Write-Host "start 通过：API http://127.0.0.1:$ApiPort；Sandbox 127.0.0.1:$SandboxPort；Worker 状态 $($workerHealth.status)。"
    if (-not $keyConfigured) { Write-Warning '模型 API Key 未配置；Worker 已启动，但真实 Task 会按设计进入 WAITING/MISSING_DEPENDENCY。' }
}
catch {
    # 只清理本次启动成功的进程；之前已运行且归属已验证的服务不被连带停止。
    foreach ($record in @($started | Sort-Object -Property role -Descending)) {
        try { Stop-ManagedProcess -Record $record | Out-Null } catch { }
    }
    throw
}
