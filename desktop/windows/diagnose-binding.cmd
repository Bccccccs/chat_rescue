@echo off
setlocal
title Chat Rescue Process Binding Diagnostics
if not exist "%~dp0runtime\python.exe" (
  echo Put this CMD and diagnose_binding.py beside ChatRescue.exe and its runtime folder.
  pause
  exit /b 1
)
"%~dp0runtime\python.exe" -B -X utf8 "%~dp0diagnose_binding.py"
set "bindingDiagnosticExit=%ERRORLEVEL%"
echo.
pause
exit /b %bindingDiagnosticExit%
