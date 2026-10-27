@echo off
rem SyncBuddy 启动入口（无控制台窗口）
rem 排查问题时用 SyncBuddy-debug.cmd，能看到输出
setlocal
rem 优先用 WorkBuddy 自带的 python，没有就用 PATH 里的 pythonw
set "PYW=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0syncbuddy.py"
