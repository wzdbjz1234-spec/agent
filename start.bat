@echo off
rem ============================================================================
rem DataHarness launcher for start.ps1.
rem
rem All logic and security boundaries live in start.ps1; this file only locates
rem PowerShell 7 and forwards arguments. The engine script relays service logs
rem through PowerShell's event queue, so this console is intentionally kept open
rem with -NoExit: closing the window stops log relay (services keep running and
rem can be stopped with stop.bat). For non-interactive use, call the engine
rem directly: pwsh -NoProfile -File start.ps1 <args>.
rem
rem Keep this file ASCII-only (see setup.bat header).
rem ============================================================================
setlocal
set "ENGINE=%~dp0start.ps1"
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
echo DataHarness: starting services. This console stays open to relay service logs.
echo Type exit to close it (services keep running; stop them with stop.bat).
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -NoExit -File "%ENGINE%" %*
set "CODE=%ERRORLEVEL%"
endlocal & exit /b %CODE%
