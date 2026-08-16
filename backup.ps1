<#
.SYNOPSIS
    创建 DataHarness 本地运行数据的可校验备份。

.DESCRIPTION
    备份只读取指定的 Runtime 数据根目录，默认是仓库下的 runtime-data，并把每个文件的
    相对路径、长度和 SHA-256 写入 manifest。备份目录必须是新目录，且不能落在源目录内，
    因此该操作不会覆盖或删除 Project、Runtime SQLite、Privacy SQLite 或发布产物。
    备份会包含 Privacy SQLite；备份介质应按用户数据和 PII 的敏感等级进行加密和保管。
#>

param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot 'runtime-data'),
    [string]$DestinationPath = (Join-Path $PSScriptRoot ('.dataharness/backups/dataharness-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')))
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Test-PathWithinRoot {
    <# 用绝对路径前缀加目录分隔符判断包含关系，避免 C:\data 与 C:\database 被误判。 #>
    param([Parameter(Mandatory = $true)][string]$Candidate, [Parameter(Mandatory = $true)][string]$Root)
    $candidateFull = [IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    return $candidateFull.Equals($rootFull, [StringComparison]::OrdinalIgnoreCase) -or
        $candidateFull.StartsWith($rootFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Get-SafeRelativePath {
    <# 备份清单只接受源根目录内的相对路径，拒绝符号链接把外部内容带入备份。 #>
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$Path)
    $relative = [IO.Path]::GetRelativePath($Root, $Path)
    if ([IO.Path]::IsPathRooted($relative) -or $relative -eq '..' -or $relative.StartsWith('..' + [IO.Path]::DirectorySeparatorChar)) {
        throw "发现越出源根目录的路径：$Path"
    }
    return $relative
}

$sourceItem = Get-Item -LiteralPath $SourceRoot -Force -ErrorAction Stop
if (-not $sourceItem.PSIsContainer) { throw "SourceRoot 必须是目录：$SourceRoot" }
if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'SourceRoot 不能是符号链接或 reparse point；请传入实际 Runtime 数据目录。'
}
$source = $sourceItem.FullName
$destination = [IO.Path]::GetFullPath($DestinationPath)
if (Test-Path -LiteralPath $destination) { throw "备份目标已存在：$destination；为避免覆盖，请使用新目录。" }
if (Test-PathWithinRoot -Candidate $destination -Root $source) {
    throw '备份目标不能位于 SourceRoot 内，否则会递归复制自身。'
}

# 先枚举并拒绝 reparse point；否则一个看似 Runtime 内的目录可能把 Host 其他路径带走。
$items = @(Get-ChildItem -LiteralPath $source -Force -Recurse)
foreach ($item in $items) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Runtime 数据中不允许备份符号链接/reparse point：$($item.FullName)"
    }
}

New-Item -ItemType Directory -Path $destination -Force | Out-Null
$dataRoot = Join-Path $destination 'data'
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
$files = @($items | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName)
$manifestFiles = [System.Collections.Generic.List[object]]::new()

foreach ($file in $files) {
    $relative = Get-SafeRelativePath -Root $source -Path $file.FullName
    $target = Join-Path $dataRoot $relative
    $targetParent = Split-Path -Parent $target
    New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sourceHash -ne $targetHash) { throw "备份校验失败：$relative" }
    $manifestFiles.Add([ordered]@{
            relative_path = $relative.Replace('\', '/')
            length = [int64]$file.Length
            sha256 = $sourceHash
        })
}

$manifest = [ordered]@{
    schema_version = 1
    created_at_utc = [DateTime]::UtcNow.ToString('o')
    source_directory_name = $sourceItem.Name
    file_count = $manifestFiles.Count
    files = @($manifestFiles)
    includes_runtime_db = $true
    includes_project_workspace = $true
    includes_privacy_db = $true
    secrets_included_by_design = $false
}
$temporaryManifest = Join-Path $destination 'backup-manifest.json.tmp'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporaryManifest -Encoding utf8NoBOM
Move-Item -LiteralPath $temporaryManifest -Destination (Join-Path $destination 'backup-manifest.json') -Force

Write-Host "备份通过：$($manifestFiles.Count) 个文件已校验并写入 $destination。"
Write-Warning '备份包含用户项目和 Privacy SQLite；请在受控、加密的存储中保管。'
