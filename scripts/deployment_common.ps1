<#
DataHarness Phase 13 本机部署脚本共用函数。

这些函数只管理由本仓库创建且带有 PID/启动时间/命令指纹的进程。任何无法证明归属的
PID 都会被视为外部进程，stop.ps1 不会触碰它。日志写入前会按环境变量名称收集敏感值并
做内存内替换，脚本本身不会打印或持久化 API Key。
#>

Set-StrictMode -Version Latest

function Get-DeploymentPaths {
    <# 返回所有部署派生目录；不把 Runtime/Privacy 数据移动到状态目录。 #>
    param([Parameter(Mandatory = $true)][string]$Root)
    $state = Join-Path $Root '.dataharness'
    [pscustomobject]@{
        Root = $Root
        State = $state
        Logs = Join-Path $state 'logs'
        Config = Join-Path $state 'config.toml'
        Manifest = Join-Path $state 'state.json'
        WorkerHealth = Join-Path $state 'worker-health.json'
        WorkerShutdown = Join-Path $state 'worker.shutdown'
        SetupMarker = Join-Path $state 'setup.json'
    }
}

function Initialize-DeploymentDirectories {
    <# 创建可重建的状态/日志目录；绝不删除 Runtime 数据。 #>
    param([Parameter(Mandatory = $true)]$Paths)
    foreach ($directory in @($Paths.State, $Paths.Logs)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
}

function Write-DeploymentJson {
    <# 用替换写入小型状态文件，避免 status.ps1 读取到半个 JSON。 #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )
    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8NoBOM
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Read-DeploymentJson {
    <# 读取状态文件；坏文件返回 null，让调用方报告可修复诊断而非误杀。 #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Test-ConfiguredModelApiKey {
    <# 只返回 [model].api_key 是否为非空值，不返回或打印密钥正文。 #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try {
        $text = Get-Content -LiteralPath $Path -Raw -Encoding utf8
    }
    catch {
        return $false
    }
    $inModel = $false
    foreach ($line in ($text -split "`r?`n")) {
        if ($line -match '^\s*\[([^\]]+)\]\s*$') {
            $inModel = $Matches[1] -eq 'model'
            continue
        }
        if ($inModel -and $line -match '^\s*api_key\s*=\s*["''](.*?)["'']\s*(?:#.*)?$') {
            return -not [string]::IsNullOrWhiteSpace($Matches[1])
        }
    }
    return $false
}

function Get-SensitiveEnvironmentValues {
    <# 只在进程内收集敏感值用于日志替换；空值和过短值不参与替换。 #>
    $values = [System.Collections.Generic.List[string]]::new()
    foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
        $name = [string]$entry.Key
        $value = [string]$entry.Value
        if ($name -match '(?i)(api.?key|secret|token|password|credential|private.?key)' -and $value.Length -ge 8) {
            $values.Add($value)
        }
    }
    return @($values | Sort-Object Length -Descending -Unique)
}

function Get-ProcessCommandLine {
    <# 查询进程命令行仅用于归属证明；不会把命令行写入日志。 #>
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        return [string]$process.CommandLine
    }
    catch {
        return ''
    }
}

function Get-ManagedProcessSnapshot {
    <# 同时校验 PID、启动时间和命令指纹，防止 PID 重用导致误杀。 #>
    param([Parameter(Mandatory = $true)]$Record)
    if ($Record -is [array]) {
        $records = @($Record | Where-Object { $null -ne $_ })
        if ($records.Count -ne 1) {
            return [pscustomobject]@{
                Role = 'unknown'
                Pid = 0
                State = 'STOPPED'
                Owned = $false
                CommandLine = ''
                Reason = '受管记录格式无效，拒绝管理'
            }
        }
        $Record = $records[0]
    }
    $result = [ordered]@{
        Role = [string]$Record.role
        Pid = [int]$Record.pid
        State = 'STOPPED'
        Owned = $false
        CommandLine = ''
        Reason = '进程不存在'
    }
    try {
        $process = Get-Process -Id ([int]$Record.pid) -ErrorAction Stop
        $commandLine = Get-ProcessCommandLine -ProcessId ([int]$Record.pid)
        $result.State = 'RUNNING'
        $result.CommandLine = $commandLine
        $start = $process.StartTime.ToUniversalTime()
        # ConvertFrom-Json 可能把 ISO 字符串反序列化为本地 DateTime；直接转 UTC，
        # 避免再次转字符串时丢失原始 Z 偏移，造成本机时区下的误报 PID 重用。
        $rawExpected = $Record.start_time_utc
        if ($rawExpected -is [DateTimeOffset]) {
            $expected = $rawExpected.UtcDateTime
        }
        elseif ($rawExpected -is [DateTime]) {
            $expected = $rawExpected.ToUniversalTime()
        }
        else {
            $expected = [DateTimeOffset]::Parse([string]$rawExpected).UtcDateTime
        }
        $sameStart = [Math]::Abs(($start - $expected).TotalSeconds) -le 3
        $sameCommand = $commandLine -match [string]$Record.command_match
        $result.Owned = $sameStart -and $sameCommand
        if (-not $sameStart) { $result.Reason = 'PID 已被不同启动时间的进程复用' }
        elseif (-not $sameCommand) { $result.Reason = '命令指纹不匹配，拒绝管理' }
        else { $result.Reason = '受管进程归属已验证' }
    }
    catch {
        $result.Reason = '进程不存在或无法读取'
    }
    return [pscustomobject]$result
}

function Normalize-ManagedProcessRecord {
    <# 将 JSON 反序列化后可能出现的单元素数组归一化为单个受管进程记录。 #>
    param([AllowNull()]$Value)
    if ($null -eq $Value) { return $null }
    $records = @($Value | Where-Object { $null -ne $_ })
    if ($records.Count -ne 1) {
        throw '受管进程记录数量不是 1；为防止误杀，拒绝继续操作。'
    }
    return $records[0]
}

function Add-RedactedLogLine {
    <# 将子进程一行输出脱敏后追加到日志；日志失败不改变子进程生命周期。 #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Line,
        [Parameter(Mandatory = $true)][object[]]$Redactions
    )
    $safe = $Line
    foreach ($secret in $Redactions) {
        if (-not [string]::IsNullOrEmpty([string]$secret)) {
            $safe = $safe.Replace([string]$secret, '<redacted>')
        }
    }
    try {
        Add-Content -LiteralPath $Path -Value $safe -Encoding utf8NoBOM
    }
    catch {
        # 诊断日志不是事实源；磁盘异常不能让管理器向屏幕打印潜在秘密。
    }
}

function Get-ManagedEventSubscriptionTable {
    <# 返回跨多次 Start-ManagedProcess 调用共享的订阅表，避免 dot-source script scope 漂移。 #>
    $variable = Get-Variable -Name DataHarnessManagedEventSubscriptions -Scope Global -ErrorAction SilentlyContinue
    if ($null -eq $variable -or $null -eq $variable.Value) {
        $table = [System.Collections.Generic.Dictionary[int, int[]]]::new()
        Set-Variable -Name DataHarnessManagedEventSubscriptions -Scope Global -Value $table
        return $table
    }
    return [System.Collections.Generic.Dictionary[int, int[]]]$variable.Value
}

function Remove-ManagedEventSubscriptions {
    <# 按 PID 清理两个输出事件订阅；清理失败不改变“禁止误杀”的判断。 #>
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    $table = Get-ManagedEventSubscriptionTable
    if (-not $table.ContainsKey($ProcessId)) { return }
    foreach ($subscriptionId in $table[$ProcessId]) {
        Unregister-Event -SubscriptionId $subscriptionId -Force -ErrorAction SilentlyContinue
    }
    [void]$table.Remove($ProcessId)
}

function Start-ManagedProcess {
    <# 启动一个带重定向日志和最小环境边界的宿主进程。 #>
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$CommandMatch,
        [hashtable]$EnvironmentOverrides = @{},
        [switch]$DropSensitiveEnvironment
    )
    if ($null -eq $EnvironmentOverrides) {
        # PowerShell 在省略可选 Hashtable 参数时可能传入 $null；后续枚举前显式归一化，
        # 避免某个角色（当前是 Worker）在同一启动器中触发空对象方法调用。
        $EnvironmentOverrides = @{}
    }
    $redactions = Get-SensitiveEnvironmentValues
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $FilePath
    $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true

    # .NET 6+ 的 ArgumentList 避免手工转义 Windows 路径；旧运行时则明确失败，
    # 因为错误的参数转义可能把配置路径或安全开关解释成另一项。
    if (-not ($info.PSObject.Properties.Name -contains 'ArgumentList')) {
        throw '需要 PowerShell 7/.NET 6 或更高版本，以安全传递部署参数。请使用 pwsh.exe。'
    }
    foreach ($argument in $ArgumentList) {
        [void]$info.ArgumentList.Add([string]$argument)
    }
    if ($DropSensitiveEnvironment) {
        foreach ($name in @($info.EnvironmentVariables.Keys)) {
            if ([string]$name -match '(?i)(api.?key|secret|token|password|credential|private.?key)') {
                [void]$info.EnvironmentVariables.Remove($name)
            }
        }
    }
    foreach ($entry in $EnvironmentOverrides.GetEnumerator()) {
        $info.EnvironmentVariables[[string]$entry.Key] = [string]$entry.Value
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) { throw "无法启动 $Role 进程" }
    # PowerShell ScriptBlock 不能直接作为 .NET 线程池事件处理器，否则 PowerShell 7
    # 会在无 Runspace 的线程中抛异常。Register-ObjectEvent 将处理放回 PowerShell
    # 事件队列，同时仍使用 Begin*ReadLine 避免 stdout/stderr 管道阻塞子进程。
    $message = @{ log = $LogPath; secrets = $redactions }
    $stdoutSubscription = $null
    $stderrSubscription = $null
    try {
        # Select-Object -First 1 明确阻断 Register-ObjectEvent 的潜在管道污染；订阅 ID
        # 只保存在表中，不作为 Start-ManagedProcess 的返回值。
        $stdoutSubscription = Register-ObjectEvent -InputObject $process -EventName OutputDataReceived -MessageData $message -Action {
            if ($null -ne $EventArgs.Data) {
                Add-RedactedLogLine -Path $Event.MessageData.log -Line $EventArgs.Data -Redactions $Event.MessageData.secrets
            }
        } -ErrorAction Stop | Select-Object -First 1
        $stderrSubscription = Register-ObjectEvent -InputObject $process -EventName ErrorDataReceived -MessageData $message -Action {
            if ($null -ne $EventArgs.Data) {
                Add-RedactedLogLine -Path $Event.MessageData.log -Line $EventArgs.Data -Redactions $Event.MessageData.secrets
            }
        } -ErrorAction Stop | Select-Object -First 1
    }
    catch {
        if ($null -ne $stdoutSubscription) { Unregister-Event -SubscriptionId $stdoutSubscription.Id -Force -ErrorAction SilentlyContinue }
        if ($null -ne $stderrSubscription) { Unregister-Event -SubscriptionId $stderrSubscription.Id -Force -ErrorAction SilentlyContinue }
        try { $process.Kill($true) } catch { try { $process.Kill() } catch { } }
        throw "${Role} 日志事件注册失败：已停止受管进程。"
    }
    if ($null -eq $stdoutSubscription -or $null -eq $stderrSubscription) {
        # 没有两个输出通道的事件订阅就不能保证日志管道不会阻塞；先清理已注册订阅，
        # 再 fail closed，不让受管进程在无人监督的情况下继续运行。
        foreach ($subscription in @($stdoutSubscription, $stderrSubscription)) {
            if ($null -ne $subscription) {
                Unregister-Event -SubscriptionId $subscription.Id -Force -ErrorAction SilentlyContinue
            }
        }
        try { $process.Kill($true) } catch { try { $process.Kill() } catch { } }
        throw "$Role 日志事件注册失败，已停止受管进程。"
    }
    $table = Get-ManagedEventSubscriptionTable
    $table[$process.Id] = @([int]$stdoutSubscription.Id, [int]$stderrSubscription.Id)
    [void]$process.BeginOutputReadLine()
    [void]$process.BeginErrorReadLine()
    Start-Sleep -Milliseconds 200
    if ($process.HasExited) {
        Remove-ManagedEventSubscriptions -ProcessId $process.Id
        throw "$Role 进程启动后立即退出，请检查 $LogPath（日志已脱敏）"
    }
    $record = [pscustomobject]@{
        role = $Role
        pid = $process.Id
        start_time_utc = $process.StartTime.ToUniversalTime().ToString('o')
        log = $LogPath
        executable = $FilePath
        arguments = @($ArgumentList)
        command_match = $CommandMatch
    }
    # 明确只向调用方返回一个 process record；事件订阅对象永不泄漏到调用方管道。
    Write-Output -NoEnumerate $record
}

function Get-ManagedProcessTreeIds {
    <# 返回受管根进程及其完整子进程树；调用方必须先验证根进程归属。 #>
    param([Parameter(Mandatory = $true)][int]$RootPid)
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $childrenByParent = @{}
    foreach ($process in $processes) {
        $parentId = [int]$process.ParentProcessId
        if (-not $childrenByParent.ContainsKey($parentId)) {
            $childrenByParent[$parentId] = [System.Collections.Generic.List[int]]::new()
        }
        $childrenByParent[$parentId].Add([int]$process.ProcessId)
    }
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    $result = [System.Collections.Generic.List[int]]::new()
    $queue.Enqueue($RootPid)
    while ($queue.Count -gt 0) {
        $currentPid = $queue.Dequeue()
        if (-not $seen.Add($currentPid)) { continue }
        $result.Add($currentPid)
        if ($childrenByParent.ContainsKey($currentPid)) {
            foreach ($childPid in $childrenByParent[$currentPid]) { $queue.Enqueue($childPid) }
        }
    }
    return @($result)
}

function Stop-ManagedProcess {
    <# 只停止已通过三重归属校验的根进程及其子进程树；未通过校验时绝不 Stop-Process。 #>
    param(
        [Parameter(Mandatory = $true)]$Record,
        [switch]$Force
    )
    $snapshot = Get-ManagedProcessSnapshot -Record $Record
    if ($snapshot.State -eq 'STOPPED') { return 'STOPPED' }
    if (-not $snapshot.Owned) {
        throw "拒绝停止 $($Record.role)：$($snapshot.Reason)"
    }
    # API、Worker 和本地 OpenSandbox Server 都没有可复用的跨进程控制协议；
    # 调用方先完成 Worker drain/Sandbox 清理，再对已证明归属的 PID 子树做终止。
    # Windows 下 uv/uvx/python 会形成多层包装进程，只停根 PID 会留下仍占端口的子进程。
    $treeIds = @(Get-ManagedProcessTreeIds -RootPid ([int]$Record.pid))
    foreach ($processId in ($treeIds | Sort-Object -Descending -Unique)) {
        try { Stop-Process -Id ([int]$processId) -Force -ErrorAction Stop } catch {
            if ($processId -ne [int]$Record.pid) { Write-Verbose "子进程 $processId 已退出或不可访问。" }
            else { throw }
        }
        Remove-ManagedEventSubscriptions -ProcessId ([int]$processId)
    }
    return 'STOPPED'
}

function Test-LocalPortAvailable {
    <# 只判断 TCP 监听冲突；不发送网络请求，不把端口探测结果当作服务健康。 #>
    param([Parameter(Mandatory = $true)][int]$Port)
    try {
        $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        return (@($listeners).Count -eq 0)
    }
    catch {
        $line = netstat -ano -p tcp 2>$null | Select-String -Pattern (":$Port\s+.*LISTENING\s+(\d+)$")
        return ($null -eq $line)
    }
}

function Test-ExternalCommand {
    <# 在明确超时时间内运行无密钥参数的诊断命令，防止 Docker/uv 卡住状态页。 #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutSeconds = 30
    )
    try {
        $info = [System.Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $FilePath
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        foreach ($argument in $Arguments) { [void]$info.ArgumentList.Add([string]$argument) }
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $info
        if (-not $process.Start()) { return [pscustomobject]@{ Ready = $false; TimedOut = $false; ExitCode = -1 } }
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill($true) } catch { try { $process.Kill() } catch { } }
            return [pscustomobject]@{ Ready = $false; TimedOut = $true; ExitCode = $null }
        }
        return [pscustomobject]@{ Ready = ($process.ExitCode -eq 0); TimedOut = $false; ExitCode = $process.ExitCode }
    }
    catch {
        return [pscustomobject]@{ Ready = $false; TimedOut = $false; ExitCode = -1 }
    }
}

function Test-TcpEndpoint {
    <# OpenSandbox 没有在本仓库固定健康 HTTP 路由，先用回环 TCP 验证可达性。 #>
    param([Parameter(Mandatory = $true)][string]$HostName, [Parameter(Mandatory = $true)][int]$Port)
    try {
        return (Test-NetConnection -ComputerName $HostName -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue)
    }
    catch {
        return $false
    }
}

function Wait-HttpReady {
    <# 等待 FastAPI readyz；响应体不写入日志，避免误带出内部信息。 #>
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutSeconds = 30
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) { return $true }
        }
        catch { }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Wait-TcpEndpoint {
    <# 等待 OpenSandbox Server 回环端口；不可达时保留明确诊断。 #>
    param(
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutSeconds = 30
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-TcpEndpoint -HostName $HostName -Port $Port) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}
