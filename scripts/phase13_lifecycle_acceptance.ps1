<#
.SYNOPSIS
    Phase 13 三进程故障定位、幂等重启和安全停止的真实本机演练。

.DESCRIPTION
    本脚本要求服务初始处于停止状态，并且只会启动随后由自己停止的受管进程。它逐个终止
    已由 status.ps1 证明归属的 Sandbox、API、Worker，验证 status 能定位故障后调用
    start.ps1 恢复。未配置模型 Key 时使用 WAITING 模式，因此不读取、生成或记录真实凭据。
#>

param(
    [string]$EvidencePath = (Join-Path $PSScriptRoot '..\.dataharness\phase13-lifecycle-acceptance.log')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pwsh = (Get-Command pwsh -ErrorAction Stop).Source
$passed = 0
$resolvedEvidencePath = [IO.Path]::GetFullPath($EvidencePath)
New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedEvidencePath) -Force | Out-Null
# 证据只记录脚本断言和部署脚本的脱敏状态输出；不会读取或写入 API Key 值。
Start-Transcript -LiteralPath $resolvedEvidencePath -Append | Out-Null

function Assert-Lifecycle {
    param([Parameter(Mandatory = $true)][bool]$Condition, [Parameter(Mandatory = $true)][string]$Name)
    if (-not $Condition) { throw "失败：$Name" }
    $script:passed++
    Write-Host "PASS $Name"
}

function Read-Status {
    $output = & $pwsh -NoProfile -File (Join-Path $root 'status.ps1') -Json
    if ($LASTEXITCODE -ne 0) { throw 'status.ps1 执行失败。' }
    return ($output | ConvertFrom-Json)
}

function Get-RoleStatus {
    param([Parameter(Mandatory = $true)]$Status, [Parameter(Mandatory = $true)][string]$Role)
    return @($Status.processes | Where-Object { $_.role -eq $Role })[0]
}

$startedByThisScript = $false
try {
    $initial = Read-Status
    $running = @($initial.processes | Where-Object { $_.state -eq 'RUNNING' })
    Assert-Lifecycle -Condition ($running.Count -eq 0) -Name '验收前没有运行中的受管服务'

    # 缺密钥必须在任何受管进程启动前 fail closed；输出仅匹配变量名和诊断文本。
    $missingKeyOutput = & $pwsh -NoProfile -File (Join-Path $root 'start.ps1') 2>&1
    Assert-Lifecycle -Condition ($LASTEXITCODE -ne 0 -and ([string]$missingKeyOutput -match '模型 API Key 未配置')) -Name '模型密钥缺失 fail-closed'

    # 错误的 Sandbox 配置也必须在服务启动前被拒绝，使用不存在的临时路径且不写入文件。
    $badConfig = Join-Path ([IO.Path]::GetTempPath()) ('dataharness-missing-' + [guid]::NewGuid().ToString('N') + '.toml')
    $badConfigOutput = & $pwsh -NoProfile -File (Join-Path $root 'start.ps1') -AllowMissingModelKey -SandboxConfigPath $badConfig 2>&1
    Assert-Lifecycle -Condition ($LASTEXITCODE -ne 0 -and ([string]$badConfigOutput -match 'OpenSandbox 配置不存在')) -Name 'OpenSandbox 配置错误 fail-closed'

    & $pwsh -NoProfile -File (Join-Path $root 'start.ps1') -AllowMissingModelKey
    if ($LASTEXITCODE -ne 0) { throw '首次 start.ps1 失败。' }
    $startedByThisScript = $true
    $ready = Read-Status
    Assert-Lifecycle -Condition ($ready.overall -eq 'READY_WAITING_MODEL_KEY') -Name '三进程启动并进入 WAITING 模式'
    foreach ($role in @('sandbox', 'api', 'worker')) {
        $roleStatus = Get-RoleStatus -Status $ready -Role $role
        Assert-Lifecycle -Condition ($roleStatus.state -eq 'RUNNING' -and $roleStatus.owned) -Name "$role 归属可证明"
    }

    & $pwsh -NoProfile -File (Join-Path $root 'start.ps1') -AllowMissingModelKey
    if ($LASTEXITCODE -ne 0) { throw '幂等 start.ps1 失败。' }
    $idempotent = Read-Status
    foreach ($role in @('sandbox', 'api', 'worker')) {
        Assert-Lifecycle -Condition ((Get-RoleStatus -Status $ready -Role $role).pid -eq (Get-RoleStatus -Status $idempotent -Role $role).pid) -Name "$role 重复启动未复制进程"
    }

    foreach ($role in @('worker', 'api', 'sandbox')) {
        $before = Read-Status
        $roleStatus = Get-RoleStatus -Status $before -Role $role
        if (-not ($roleStatus.owned -and $roleStatus.state -eq 'RUNNING' -and $null -ne $roleStatus.pid)) {
            throw "拒绝演练 $role：无法证明 PID 归属。"
        }
        # 仅终止 status 已验证归属的当前受管 PID，模拟该进程异常退出。
        Stop-Process -Id ([int]$roleStatus.pid) -Force -ErrorAction Stop
        Start-Sleep -Milliseconds 500
        $failedStatus = Read-Status
        $failedRole = Get-RoleStatus -Status $failedStatus -Role $role
        Assert-Lifecycle -Condition ($failedStatus.overall -eq 'NOT_READY' -and $failedRole.state -eq 'STOPPED') -Name "$role 异常可由 status 定位"

        & $pwsh -NoProfile -File (Join-Path $root 'start.ps1') -AllowMissingModelKey
        if ($LASTEXITCODE -ne 0) { throw "$role 异常后的 start.ps1 恢复失败。" }
        $recovered = Read-Status
        $recoveredRole = Get-RoleStatus -Status $recovered -Role $role
        Assert-Lifecycle -Condition ($recovered.overall -eq 'READY_WAITING_MODEL_KEY' -and $recoveredRole.owned -and $recoveredRole.pid -ne $roleStatus.pid) -Name "$role 异常后可重启恢复"
    }
}
finally {
    if ($startedByThisScript) {
        & $pwsh -NoProfile -File (Join-Path $root 'stop.ps1') -DrainTimeoutSeconds 15 -Force
        if ($LASTEXITCODE -ne 0) { throw '验收清理 stop.ps1 失败。' }
        $stopped = Read-Status
        Assert-Lifecycle -Condition (@($stopped.processes | Where-Object { $_.state -eq 'RUNNING' }).Count -eq 0) -Name '安全停止后无受管残留进程'
    }
    Stop-Transcript | Out-Null
}

Write-Host "Phase 13 生命周期验收通过：$passed 项。证据：$resolvedEvidencePath"
