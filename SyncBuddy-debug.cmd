@echo off
rem 排查用：带控制台，能看到全部输出和异常
chcp 65001 >nul 2>&1
setlocal
rem 优先用 WorkBuddy 自带的 python，没有就用 PATH 里的 python
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0syncbuddy.py" %*
pause
