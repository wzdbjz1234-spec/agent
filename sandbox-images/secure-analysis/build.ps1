<#
.SYNOPSIS
Build and record auditable image evidence for secure-analysis.

.DESCRIPTION
Refuses mutable tags and unlocked base images. Docker, SBOM and vulnerability
scanners are never faked in source; callers must write the actual digest, SBOM
and scan results into build-evidence/ for phase acceptance to reference.

NOTE: keep this file ASCII-only. PowerShell 5.1 reads BOM-less .ps1 as ANSI,
and the edit tool does not preserve a UTF-8 BOM, so non-ASCII text in this
script breaks parsing on this platform.
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
    throw 'Docker is unavailable; refusing to claim an unbuilt image as secure-analysis.'
}

# PowerShell 5.1 treats native stderr as ErrorRecords: docker build progress goes
# to stderr and would be mistaken for a terminating error under 'Stop'. Route the
# command through cmd /c with file redirection so stderr never enters the
# PowerShell error stream, then judge success by the exit code.
$buildLog = Join-Path $env:TEMP 'secure-analysis-build.log'
& cmd.exe /c "docker build --pull --build-arg `"BASE_IMAGE=$BaseImage`" --tag $Tag `"$PSScriptRoot`" > `"$buildLog`" 2>&1"
$buildExit = $LASTEXITCODE
Get-Content $buildLog | Write-Output
if ($buildExit -ne 0) {
    throw "docker build failed (exit $buildExit). See $buildLog"
}
$digest = docker image inspect $Tag --format '{{index .RepoDigests 0}}'
if ($digest -notmatch '@sha256:[0-9a-f]{64}$') {
    throw 'Build result did not return a locked digest.'
}
# Record the bare content digest; SandboxSpec.image_digest requires "sha256:...".
$digest = $digest.Substring($digest.IndexOf('@') + 1)

$evidence = Join-Path $PSScriptRoot 'build-evidence'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
Set-Content -NoNewline -Encoding ascii -Path (Join-Path $evidence 'image-digest.txt') -Value $digest
Write-Output "Built $digest. Write SBOM and vulnerability scan results into $evidence."
