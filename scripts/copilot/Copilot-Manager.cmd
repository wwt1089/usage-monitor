@echo off
rem Copilot 권한 관리 화면 실행 (더블클릭)
start "" powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Minimized -File "%~dp0Copilot-Manager-GUI.ps1"
