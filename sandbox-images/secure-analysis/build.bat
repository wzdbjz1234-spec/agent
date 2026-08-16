@echo off
rem ============================================================================
rem DataHarness launcher for build.ps1 (secure-analysis image).
rem
rem This file only locates PowerShell 7 and forwards arguments plus exit code to
rem the .ps1 engine script; digest-lock checks and build logic live in build.ps1.
rem Keep this file ASCII-only (see setup.bat header).
rem ============================================================================
setlocal
set "ENGINE=%~dp0build.ps1"
set "PWSH="

if defined DATAHARNESS_PWSH if exist "%DATAHARNESS_PWSH%" set "PWSH=%DATAHARNESS_PWSH%"
if defined PWSH goto run

where pwsh >nul 2>nul
if not errorlevel 1 set "PWSH=pwsh"
if defined PWSH goto run

if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if defined PWSH goto run

if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe" set "PWSH=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe"
if defined PWSH goto run

echo [ERROR] PowerShell 7 (pwsh.exe) is required but was not found.
echo Install it from https://github.com/PowerShell/PowerShell/releases
echo or set DATAHARNESS_PWSH to the full path of pwsh.exe.
endlocal & exit /b 1

:run
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%ENGINE%" %*
set "CODE=%ERRORLEVEL%"
endlocal & exit /b %CODE%
