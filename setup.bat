@echo off
rem ============================================================================
rem DataHarness launcher for setup.ps1.
rem
rem This file only locates PowerShell 7 and forwards arguments plus exit code to
rem the .ps1 engine script; ALL logic, preflight checks and security boundaries
rem live in setup.ps1. Keep this file ASCII-only: cmd.exe parses batch files with
rem the console ANSI codepage, and non-ASCII bytes break parsing on this platform.
rem
rem pwsh lookup order:
rem   1. DATAHARNESS_PWSH environment variable (full path to pwsh.exe)
rem   2. pwsh on PATH
rem   3. %ProgramFiles%\PowerShell\7\pwsh.exe
rem   4. dev-machine fallback: pwsh bundled by the Codex runtime
rem ============================================================================
setlocal
set "ENGINE=%~dp0setup.ps1"
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
