<#
.SYNOPSIS
    从 DataHarness 校验备份恢复到一个明确指定的空目录。

.DESCRIPTION
    restore.ps1 先验证清单、每个备份文件的 SHA-256 和相对路径，再复制到用户显式指定的
    空目录。它从不就地覆盖 runtime-data，也不删除任何目标内容；要切换到恢复的数据，
    请在停止服务后由用户在配置中明确指向已恢复目录。
#>

param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [Parameter(Mandatory = $true)][string]$DestinationRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Test-SafeManifestRelativePath {
    <# 清单来自可移动介质，恢复前必须拒绝绝对路径和 ..，防止路径穿越写入 Host。 #>
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    $normal = $RelativePath.Replace('/', [IO.Path]::DirectorySeparatorChar)
    if ([IO.Path]::IsPathRooted($normal) -or $normal -eq '..' -or $normal.StartsWith('..' + [IO.Path]::DirectorySeparatorChar)) {
        throw "备份清单含不安全相对路径：$RelativePath"
    }
    return $normal
}

function Test-TargetWithinRoot {
    <# 最终路径必须落在明确的恢复目标根内，避免 Join-Path 的特殊路径语义绕出目录。 #>
    param([Parameter(Mandatory = $true)][string]$Target, [Parameter(Mandatory = $true)][string]$Root)
    $targetFull = [IO.Path]::GetFullPath($Target)
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    return $targetFull.StartsWith($rootFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

$backup = (Get-Item -LiteralPath $BackupPath -Force -ErrorAction Stop)
if (-not $backup.PSIsContainer) { throw "BackupPath 必须是目录：$BackupPath" }
$manifestPath = Join-Path $backup.FullName 'backup-manifest.json'
$dataRoot = Join-Path $backup.FullName 'data'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or -not (Test-Path -LiteralPath $dataRoot -PathType Container)) {
    throw '备份缺少 backup-manifest.json 或 data 目录。'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
if ([int]$manifest.schema_version -ne 1 -or $null -eq $manifest.files) { throw '不支持或损坏的备份清单。' }
$destination = [IO.Path]::GetFullPath($DestinationRoot)
if (Test-Path -LiteralPath $destination) {
    $existing = @(Get-ChildItem -LiteralPath $destination -Force)
    if ($existing.Count -ne 0) { throw "恢复目标必须为空目录：$destination；restore 不覆盖现有数据。" }
}
else {
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
}

$seen = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$validated = [System.Collections.Generic.List[object]]::new()
foreach ($entry in @($manifest.files)) {
    $relative = Test-SafeManifestRelativePath -RelativePath ([string]$entry.relative_path)
    if (-not $seen.Add($relative)) { throw "备份清单含重复路径：$relative" }
    $source = Join-Path $dataRoot $relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "备份缺少文件：$relative" }
    $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne [string]$entry.sha256 -or [int64](Get-Item -LiteralPath $source).Length -ne [int64]$entry.length) {
        throw "备份完整性校验失败：$relative"
    }
    $target = Join-Path $destination $relative
    if (-not (Test-TargetWithinRoot -Target $target -Root $destination)) { throw "恢复目标越界：$relative" }
    $validated.Add([pscustomobject]@{ Source = $source; Relative = $relative; Target = $target })
}
if ($validated.Count -ne [int]$manifest.file_count) { throw '备份清单 file_count 与实际条目数不一致。' }

# 所有输入都已经校验通过后才开始写入，尽可能避免在损坏备份下留下半恢复数据。
foreach ($entry in $validated) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $entry.Target) -Force | Out-Null
    Copy-Item -LiteralPath $entry.Source -Destination $entry.Target -Force
}
Write-Host "恢复通过：$($validated.Count) 个文件已写入 $destination；未覆盖或删除其他用户数据。"
