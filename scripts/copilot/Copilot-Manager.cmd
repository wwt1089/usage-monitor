@echo off
rem Launch Copilot Manager GUI (double-click)
start "" powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Minimized -File "%~dp0Copilot-Manager-GUI.ps1"
