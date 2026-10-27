#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2026 Veg0s
#
"""
syncbuddy / _proc.py — 子进程调用的统一出口

为什么需要它：
    SyncBuddy 用 pythonw.exe 启动，进程本身没有控制台。
    在这种情况下，任何 console 子程序（tasklist / git / python / ssh）
    都会自己分配一个新控制台窗口 —— 于是黑框一个个往外蹦。

    watch.py 每 2 秒调一次 tasklist，用户看到的就是「不断弹出 cmd」。

    CREATE_NO_WINDOW 让子进程不创建控制台窗口，且该状态会被孙进程继承
    （git → ssh 也不会弹）。对 GUI 程序（WorkBuddy.exe）无副作用。

所以：所有 subprocess 调用一律走这里，不要直接调 subprocess。
"""

import atexit
import os
import subprocess
import sys

# Windows 专用常量；其它平台为 0，传进去也无害
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def kill_tree():
    """
    退出时清掉整棵进程树。

    后台线程里跑着 git fetch（180s 超时）/ git push（300s 超时）。
    用户点退出时如果正好卡在网络等待，Python 主进程走了，git.exe 和它
    派生出来的 ssh.exe 会留在系统里 —— 表现为「关了程序，任务管理器里
    还有几个 git 进程」。

    taskkill /T 按父 PID 递归终止，只影响自己这棵树，不会误伤用户在
    别的窗口里跑的 git。att 退出阶段执行，日志是行缓冲的不会丢。
    """
    if sys.platform != "win32":
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(os.getpid())],
                       creationflags=CREATE_NO_WINDOW,
                       capture_output=True, timeout=10)
    except Exception:
        pass


def run(cmd, **kwargs):
    """替代 subprocess.run —— 不弹窗。"""
    kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    return subprocess.run(cmd, **kwargs)


def popen(cmd, **kwargs):
    """替代 subprocess.Popen —— 不弹窗。"""
    kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    return subprocess.Popen(cmd, **kwargs)


def check(cmd, **kwargs):
    """替代 subprocess.check_output —— 不弹窗。"""
    kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    return subprocess.check_output(cmd, **kwargs)
