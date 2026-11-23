@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
start "" "%~dp0运行环境\Scripts\pythonw.exe" "%~dp0工具\memotrace_local.py" gui
