<#
.SYNOPSIS
    Phase 13 无外部服务的可复现验收脚本。

.DESCRIPTION
    该脚本只做 PowerShell 语法、部署共用函数、PID 防误杀、敏感日志脱敏、端口冲突、
    子进程超时、备份/恢复和 status mock 验收。它不会启动 Docker/OpenSandbox/API/Worker，
    也不会连接云模型；三进程异常恢复由 phase13_lifecycle_acceptance.ps1 单独演练。
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $root 'scripts/deployment_common.ps1')

$passed = 0
$failed = 0
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("dataharness-phase13-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $temporaryRoot | Out-Null
$logPath = Join-Path $temporaryRoot 'managed.log'
$secretName = 'DATAHARNESS_PHASE13_TEST_SECRET'
$secretValue = 'phase13-static-test-secret-7f9c'

function Assert-Phase13 {
    param([Parameter(Mandatory = $true)][bool]$Condition, [Parameter(Mandatory = $true)][string]$Name)
    if (-not $Condition) { throw "失败：$Name" }
    $script:passed++
    Write-Host "PASS $Name"
}

try {
    # 1. 所有入口先过 PowerShell parser，避免把中文诊断或参数转义错误留到用户启动时。
    $parseTargets = @(
        (Join-Path $root 'setup.ps1'),
        (Join-Path $root 'start.ps1'),
        (Join-Path $root 'stop.ps1'),
        (Join-Path $root 'status.ps1'),
        (Join-Path $root 'backup.ps1'),
        (Join-Path $root 'restore.ps1'),
        (Join-Path $root 'scripts/phase13_lifecycle_acceptance.ps1'),
        (Join-Path $root 'scripts/deployment_common.ps1')
    )
    foreach ($target in $parseTargets) {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($target, [ref]$tokens, [ref]$errors) | Out-Null
        Assert-Phase13 -Condition (@($errors).Count -eq 0) -Name "PowerShell 语法：$(Split-Path $target -Leaf)"
    }

    # 2. 端口冲突判断只使用本机 loopback listener，不访问外部服务。
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    Assert-Phase13 -Condition (-not (Test-LocalPortAvailable -Port $port)) -Name '端口占用可检测'
    $listener.Stop()
    Assert-Phase13 -Condition (Test-LocalPortAvailable -Port $port) -Name '端口释放可检测'

    # 3. 启动一个短生命周期本机 PowerShell 子进程，验证 PID/启动时间/命令指纹和日志脱敏。
    Set-Item -Path "Env:$secretName" -Value $secretValue
    $pwsh = (Get-Command pwsh -ErrorAction Stop).Source
    $childCode = "[Console]::WriteLine(`$env:$secretName); Start-Sleep -Seconds 5; # phase13-marker"
    $record = Start-ManagedProcess `
        -Role 'test-child' `
        -FilePath $pwsh `
        -ArgumentList @('-NoProfile', '-Command', $childCode) `
        -WorkingDirectory $temporaryRoot `
        -LogPath $logPath `
        -CommandMatch 'phase13-marker'
    $owned = Get-ManagedProcessSnapshot -Record $record
    Assert-Phase13 -Condition ($owned.Owned -and $owned.State -eq 'RUNNING') -Name 'PID/启动时间/命令指纹归属校验'
    $wrong = $record | Select-Object *
    $wrong.command_match = 'never-match-outside-process'
    $unowned = Get-ManagedProcessSnapshot -Record $wrong
    Assert-Phase13 -Condition (-not $unowned.Owned) -Name '不匹配命令拒绝管理，防止误杀'
    $logDeadline = [DateTime]::UtcNow.AddSeconds(3)
    $safeLog = $false
    while ([DateTime]::UtcNow -lt $logDeadline) {
        if (Test-Path $logPath) {
            $text = Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue
            if ($text -match '<redacted>' -and $text -notmatch [regex]::Escape($secretValue)) { $safeLog = $true; break }
        }
        Start-Sleep -Milliseconds 100
    }
    Assert-Phase13 -Condition $safeLog -Name '敏感环境变量不落日志'
    Stop-ManagedProcess -Record $record | Out-Null
    Start-Sleep -Milliseconds 150
    Assert-Phase13 -Condition ((Get-ManagedProcessSnapshot -Record $record).State -eq 'STOPPED') -Name '受管子进程可安全停止'
    Remove-Item -Path "Env:$secretName" -ErrorAction SilentlyContinue

    # 4. 子进程超时必须返回而不是无限等待；命令只在本机运行。
    $timeoutResult = Test-ExternalCommand -FilePath $pwsh -Arguments @('-NoProfile', '-Command', 'Start-Sleep -Seconds 10') -TimeoutSeconds 1
    Assert-Phase13 -Condition ($timeoutResult.TimedOut -and -not $timeoutResult.Ready) -Name '子进程超时 fail-closed'

    # 5. 不可达 TCP/HTTP 的等待有上限；使用本机保留端口，不连接 OpenSandbox。
    $closedPort = 65530
    $tcpResult = Wait-TcpEndpoint -HostName '127.0.0.1' -Port $closedPort -TimeoutSeconds 1
    Assert-Phase13 -Condition (-not $tcpResult) -Name 'OpenSandbox 不可达在有界时间内失败'
    $httpResult = Wait-HttpReady -Uri 'http://127.0.0.1:65530/readyz' -TimeoutSeconds 1
    Assert-Phase13 -Condition (-not $httpResult) -Name 'API 不可达在有界时间内失败'

    # 6. status 的 mock 模式只读本地状态，不触发 Docker/API/Sandbox 网络探测。
    $statusOutput = & $pwsh -NoProfile -File (Join-Path $root 'status.ps1') -Json -NoExternalChecks 2>&1
    Assert-Phase13 -Condition ($LASTEXITCODE -eq 0 -and ([string]$statusOutput -match 'NOT_READY|READY')) -Name 'status.ps1 mock 诊断可复现'

    # 7. 备份/恢复只操作测试目录：恢复到空目录后检查 hash，并确认源文件没有被脚本改写。
    $runtimeRoot = Join-Path $temporaryRoot 'runtime-data'
    $backupRoot = Join-Path $temporaryRoot 'backup'
    $restoreRoot = Join-Path $temporaryRoot 'restored-runtime-data'
    New-Item -ItemType Directory -Path (Join-Path $runtimeRoot 'projects/project-1') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $runtimeRoot 'privacy') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $runtimeRoot 'runtime.db') -Value 'synthetic-runtime-state' -NoNewline -Encoding utf8NoBOM
    Set-Content -LiteralPath (Join-Path $runtimeRoot 'projects/project-1/artifact.txt') -Value 'synthetic-artifact' -NoNewline -Encoding utf8NoBOM
    Set-Content -LiteralPath (Join-Path $runtimeRoot 'privacy/task-1.db') -Value 'synthetic-privacy-map' -NoNewline -Encoding utf8NoBOM
    & $pwsh -NoProfile -File (Join-Path $root 'backup.ps1') -SourceRoot $runtimeRoot -DestinationPath $backupRoot
    Assert-Phase13 -Condition ($LASTEXITCODE -eq 0 -and (Test-Path (Join-Path $backupRoot 'backup-manifest.json'))) -Name 'Runtime/Project/Privacy 备份可验证'
    & $pwsh -NoProfile -File (Join-Path $root 'restore.ps1') -BackupPath $backupRoot -DestinationRoot $restoreRoot
    $sourceHash = (Get-FileHash -LiteralPath (Join-Path $runtimeRoot 'projects/project-1/artifact.txt') -Algorithm SHA256).Hash
    $restoredHash = (Get-FileHash -LiteralPath (Join-Path $restoreRoot 'projects/project-1/artifact.txt') -Algorithm SHA256).Hash
    Assert-Phase13 -Condition ($LASTEXITCODE -eq 0 -and $sourceHash -eq $restoredHash) -Name '备份恢复不改写源数据'

    # 8. .bat 启动器包装验证：包装只负责定位 pwsh 并转发参数和退出码，逻辑全部保留在
    # .ps1 引擎中。这里检查包装存在、指向对应引擎，并通过 status.bat 端到端确认参数
    # 转发，通过 restore.bat 的失败用例确认退出码转发。
    $batWrappers = @(
        @{ Bat = 'setup.bat'; Engine = 'setup.ps1' }
        @{ Bat = 'start.bat'; Engine = 'start.ps1' }
        @{ Bat = 'stop.bat'; Engine = 'stop.ps1' }
        @{ Bat = 'status.bat'; Engine = 'status.ps1' }
        @{ Bat = 'backup.bat'; Engine = 'backup.ps1' }
        @{ Bat = 'restore.bat'; Engine = 'restore.ps1' }
        @{ Bat = 'scripts/phase13_acceptance.bat'; Engine = 'phase13_acceptance.ps1' }
        @{ Bat = 'scripts/phase13_lifecycle_acceptance.bat'; Engine = 'phase13_lifecycle_acceptance.ps1' }
        @{ Bat = 'sandbox-images/secure-analysis/build.bat'; Engine = 'build.ps1' }
    )
    foreach ($wrapper in $batWrappers) {
        $wrapperPath = Join-Path $root $wrapper.Bat
        Assert-Phase13 -Condition (Test-Path -LiteralPath $wrapperPath -PathType Leaf) -Name ".bat 包装存在：$($wrapper.Bat)"
        $wrapperText = Get-Content -LiteralPath $wrapperPath -Raw
        Assert-Phase13 -Condition ($wrapperText.Contains($wrapper.Engine)) -Name ".bat 包装指向引擎：$($wrapper.Engine)"
        Assert-Phase13 -Condition ($wrapperText -match '(?i)pwsh') -Name ".bat 包装调用 pwsh：$($wrapper.Bat)"
    }
    $statusBatOutput = & cmd.exe /c ('"{0}" -Json -NoExternalChecks' -f (Join-Path $root 'status.bat')) 2>&1
    Assert-Phase13 -Condition ($LASTEXITCODE -eq 0 -and ([string]$statusBatOutput -match 'NOT_READY|READY')) -Name 'status.bat 端到端转发参数和退出码'
    $restoreBatOutput = & cmd.exe /c ('"{0}" -BackupPath C:\dataharness-nonexistent-backup -DestinationRoot C:\dataharness-nonexistent-restore' -f (Join-Path $root 'restore.bat')) 2>&1
    Assert-Phase13 -Condition ($LASTEXITCODE -ne 0) -Name 'restore.bat 失败退出码转发'

    Write-Host "Phase 13 静态/模拟验收通过：$passed 项。未启动 Docker/OpenSandbox/API/Worker。"
    exit 0
}
catch {
    $failed++
    Write-Error $_
    Write-Host "Phase 13 静态/模拟验收失败：通过=$passed，失败=$failed。"
    exit 1
}
finally {
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path "Env:$secretName" -ErrorAction SilentlyContinue
}
