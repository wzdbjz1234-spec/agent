<#
.SYNOPSIS
构建并记录 secure-analysis 的可审计镜像证据。

.DESCRIPTION
该脚本拒绝可变 tag 和未锁定的 base image。Docker、SBOM 与漏洞扫描工具并不在源码中
伪造；调用后须将实际 digest、SBOM 和扫描结果写入 build-evidence/，再由阶段验收引用。
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[^@\s]+@sha256:[0-9a-f]{64}$')]
    [string]$BaseImage,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9._/-]*:[a-z0-9._-]+$')]
    [string]$Tag
)

$ErrorActionPreference = 'Stop'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker 不可用；拒绝把未经构建和扫描的镜像声明为 secure-analysis。'
}

docker build --pull --build-arg "BASE_IMAGE=$BaseImage" --tag $Tag $PSScriptRoot
$digest = docker image inspect $Tag --format '{{index .RepoDigests 0}}'
if ($digest -notmatch '@sha256:[0-9a-f]{64}$') {
    throw '构建结果未返回锁定 digest。'
}

$evidence = Join-Path $PSScriptRoot 'build-evidence'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
Set-Content -NoNewline -Encoding utf8 -Path (Join-Path $evidence 'image-digest.txt') -Value $digest
Write-Output "已构建 $digest。请使用部署批准的 SBOM 和漏洞扫描器将结果写入 $evidence。"
