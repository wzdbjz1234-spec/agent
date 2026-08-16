<#
.SYNOPSIS
    安全停止 DataHarness 本机服务。

.DESCRIPTION
    先创建 Worker shutdown marker，阻止领取新 Run；若有当前 Run，则通过本机 API 写入
    取消意图并等待 Worker 自己完成 Sandbox 清理和 Runtime 终态收口。默认超时后保留进程
    并返回失败，避免误杀或留下未知外部执行；只有显式 -Force 才会终止已验证归属的 PID。
    本脚本不删除 runtime-data、Privacy DB、Project Workspace 或发布产物。
#>

param(
    [int]$DrainTimeoutSeconds = 30,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'scripts/deployment_common.ps1')

$root = (Resolve-Path $PSScriptRoot).Path
$paths = Get-DeploymentPaths -Root $root
$manifest = Read-DeploymentJson -Path $paths.Manifest
if ($null -eq $manifest -or $null -eq $manifest.roles) {
    Write-Host 'stop 幂等完成：没有受管服务状态。未删除任何用户数据。'
    exit 0
}
if ($DrainTimeoutSeconds -lt 1) { throw 'DrainTimeoutSeconds 必须至少为 1 秒。' }

function Get-RoleRecord {
    param([Parameter(Mandatory = $true)][string]$Role)
    $property = $manifest.roles.PSObject.Properties[$Role]
    if ($null -eq $property) { return $null }
    return Normalize-ManagedProcessRecord -Value $property.Value
}

$worker = Get-RoleRecord -Role 'worker'
$api = Get-RoleRecord -Role 'api'
$sandbox = Get-RoleRecord -Role 'sandbox'
$workerSnapshot = if ($null -ne $worker) { Get-ManagedProcessSnapshot -Record $worker } else { $null }

if ($null -ne $worker -and $null -ne $workerSnapshot -and $workerSnapshot.State -eq 'RUNNING') {
    if (-not $workerSnapshot.Owned) {
        throw "拒绝停止 Worker：PID $($worker.pid) 无法通过启动时间和命令指纹证明归属，防止误杀项目外进程。"
    }
    # marker 是停止领取新任务的唯一控制信号；它不会把 prompt 或密钥写入 Runtime。
    New-Item -ItemType File -Force -Path $paths.WorkerShutdown | Out-Null
    $health = Read-DeploymentJson -Path $paths.WorkerHealth
    if ($null -ne $health -and [string]$health.active_task_id -and $null -ne $api) {
        $apiSnapshot = Get-ManagedProcessSnapshot -Record $api
        if ($apiSnapshot.State -eq 'RUNNING' -and $apiSnapshot.Owned) {
            try {
                # 只发送稳定 Task ID；响应体不写日志，API 也不会接收模型 Key。
                Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$([int]$manifest.api_port)/tasks/$([string]$health.active_task_id)/cancel" -TimeoutSec 3 -ErrorAction Stop | Out-Null
                Write-Host "已为当前 Task 写入取消意图，等待 Worker 安全清理 Sandbox。"
            }
            catch {
                Write-Warning '无法通过 API 写入取消意图；Worker 仍会停止领取新任务，继续等待其自然收口。'
            }
        }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds($DrainTimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        $workerSnapshot = Get-ManagedProcessSnapshot -Record $worker
    } while ($workerSnapshot.State -eq 'RUNNING' -and [DateTime]::UtcNow -lt $deadline)
    if ($workerSnapshot.State -eq 'RUNNING') {
        if (-not $Force) {
            throw "Worker 在 ${DrainTimeoutSeconds}s 内未完成 drain；为防止误杀，API/Sandbox 保持运行。可继续等待、检查 .dataharness/logs/worker.log，或明确使用 -Force。"
        }
        Write-Warning '显式 -Force：Worker 未在 drain 超时内退出，将只终止已验证归属的 Worker PID；未删除用户数据。'
        Stop-ManagedProcess -Record $worker -Force | Out-Null
    }
}

# Worker 已停止后才停止 OpenSandbox，给 Executor 留出 terminate Sandbox 的机会。
foreach ($record in @($sandbox, $api)) {
    if ($null -eq $record) { continue }
    $snapshot = Get-ManagedProcessSnapshot -Record $record
    if ($snapshot.State -eq 'RUNNING') {
        if (-not $snapshot.Owned) {
            throw "拒绝停止 $($record.role)：$($snapshot.Reason)。不会触碰外部进程。"
        }
        Stop-ManagedProcess -Record $record | Out-Null
    }
}

$stopped = Read-DeploymentJson -Path $paths.Manifest
if ($null -ne $stopped) {
    $stopped | Add-Member -NotePropertyName stopped_at_utc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
    $stopped | Add-Member -NotePropertyName data_deletion_performed -NotePropertyValue $false -Force
    Write-DeploymentJson -Path $paths.Manifest -Value $stopped
}
Write-Host 'stop 通过：Worker drain、Sandbox、API 已按顺序停止；用户数据未删除。'
