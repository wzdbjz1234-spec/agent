<#
.SYNOPSIS
    输出 DataHarness 三进程、Docker、端口、模型密钥和 WebUI 构建诊断。

.DESCRIPTION
    status.ps1 是只读诊断命令，不启动服务、不删除数据、不读取 API Key 值。PID 只有在
    启动时间和命令指纹同时匹配时才显示为受管；Docker 检查最多等待 30 秒，避免诊断命令
    无限卡住。-Json 适合自动化验收，默认人类输出不包含命令行或秘密。
#>

param(
    [switch]$Json,
    [switch]$Strict,
    [switch]$NoExternalChecks
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'scripts/deployment_common.ps1')

$root = (Resolve-Path $PSScriptRoot).Path
$paths = Get-DeploymentPaths -Root $root
$setup = Read-DeploymentJson -Path $paths.SetupMarker
$manifest = Read-DeploymentJson -Path $paths.Manifest

function Get-RoleRecord {
    param([Parameter(Mandatory = $true)][string]$Role)
    if ($null -eq $manifest -or $null -eq $manifest.roles) { return $null }
    $property = $manifest.roles.PSObject.Properties[$Role]
    if ($null -eq $property) { return $null }
    return Normalize-ManagedProcessRecord -Value $property.Value
}

function Get-RoleStatus {
    param([Parameter(Mandatory = $true)][string]$Role)
    $record = Get-RoleRecord -Role $Role
    if ($null -eq $record) {
        return [pscustomobject]@{ role = $Role; state = 'NOT_CONFIGURED'; owned = $false; pid = $null; reason = '没有 setup/start 状态记录' }
    }
    $snapshot = Get-ManagedProcessSnapshot -Record $record
    return [pscustomobject]@{
        role = $Role
        state = [string]$snapshot.State
        owned = [bool]$snapshot.Owned
        pid = if ($snapshot.State -eq 'RUNNING' -and $snapshot.Owned) { [int]$record.pid } else { $null }
        reason = [string]$snapshot.Reason
    }
}

$roles = @('sandbox', 'api', 'worker') | ForEach-Object { Get-RoleStatus -Role $_ }
$docker = if ($NoExternalChecks) {
    [pscustomobject]@{ Ready = $false; TimedOut = $false; ExitCode = $null }
}
elseif (Get-Command docker -ErrorAction SilentlyContinue) {
    Test-ExternalCommand -FilePath ((Get-Command docker).Source) -Arguments @('info') -TimeoutSeconds 30
}
else {
    [pscustomobject]@{ Ready = $false; TimedOut = $false; ExitCode = -1 }
}
$health = Read-DeploymentJson -Path $paths.WorkerHealth
$workerHealth = if ($null -ne $health -and [string]$health.status) { [string]$health.status } else { 'HEARTBEAT_UNAVAILABLE' }
$apiPort = if ($null -ne $setup) { [int]$setup.api_port } else { $null }
$sandboxPort = if ($null -ne $setup) { [int]$setup.sandbox_port } else { $null }
$apiReady = if (-not $NoExternalChecks -and $null -ne $apiPort) { Wait-HttpReady -Uri "http://127.0.0.1:$apiPort/readyz" -TimeoutSeconds 2 } else { $false }
$sandboxReachable = if (-not $NoExternalChecks -and $null -ne $sandboxPort) { Test-TcpEndpoint -HostName '127.0.0.1' -Port $sandboxPort } else { $false }
$keyConfigured = if ($null -ne $setup) {
    Test-ConfiguredModelApiKey -Path ([string]$setup.config_path)
}
else {
    $false
}
$webBuilt = Test-Path -LiteralPath (Join-Path $root 'web/dist/index.html') -PathType Leaf
$allOwned = (@($roles | Where-Object { -not $_.owned }).Count -eq 0)
$ready = $null -ne $setup -and $allOwned -and $apiReady -and $sandboxReachable -and $docker.Ready -and $webBuilt
$overall = if ($ready -and $keyConfigured) { 'READY' } elseif ($ready) { 'READY_WAITING_MODEL_KEY' } elseif ($docker.TimedOut) { 'BLOCKED_DOCKER_TIMEOUT' } else { 'NOT_READY' }

$result = [ordered]@{
    schema_version = 1
    overall = $overall
    setup_present = ($null -ne $setup)
    docker = [ordered]@{ ready = [bool]$docker.Ready; timed_out = [bool]$docker.TimedOut; timeout_seconds = 30; external_checks_skipped = $NoExternalChecks }
    web = [ordered]@{ built = $webBuilt; node_required_at_runtime = $false }
    model = [ordered]@{ api_key_configured = $keyConfigured; api_key_value_exposed = $false }
    api = [ordered]@{ port = $apiPort; ready = $apiReady; host = '127.0.0.1' }
    sandbox = [ordered]@{ port = $sandboxPort; tcp_reachable = $sandboxReachable; host = '127.0.0.1' }
    worker = [ordered]@{ health = $workerHealth; health_file_present = ($null -ne $health) }
    processes = @($roles)
    data_deletion_performed = $false
}

if ($Json) {
    $result | ConvertTo-Json -Depth 8
}
else {
    Write-Host "DataHarness 状态：$overall"
    Write-Host "Docker：$([string]$result.docker.ready)（超时=$([string]$result.docker.timed_out)，上限=30s）"
    Write-Host "WebUI：$([string]$result.web.built)（运行时不需要 Node.js）"
    Write-Host "模型 API Key：$(if ($keyConfigured) { '已配置' } else { '未配置' })（只显示状态）"
    Write-Host "API：$([string]$result.api.ready) 127.0.0.1:$apiPort"
    Write-Host "OpenSandbox TCP：$([string]$result.sandbox.tcp_reachable) 127.0.0.1:$sandboxPort"
    Write-Host "Worker 心跳：$workerHealth"
    foreach ($role in $roles) { Write-Host ("{0}: state={1}; owned={2}; pid={3}; {4}" -f $role.role, $role.state, $role.owned, $role.pid, $role.reason) }
}

if ($Strict -and $overall -notin @('READY', 'READY_WAITING_MODEL_KEY')) { exit 1 }
exit 0
