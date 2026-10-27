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
syncbuddy / watch.py — WorkBuddy 进程监听

为什么要专门写这个：WorkBuddy 是 Electron，一次启动有 8 个进程。
所以不能靠「等主进程退出」判断关闭，只能看进程总数的边沿。

三个必须处理的点：
  1. 边沿检测：0→N 视为启动，N→0 视为退出
  2. 防抖：Electron 刷新/崩溃重启时进程会短暂归零，必须连续 debounce 次为空才算真退出
  3. 任何入口都能捕获：不管从任务栏、开始菜单还是 SyncBuddy 启动，都走同一套判断

判定逻辑抽成 step()，便于注入假进程数做回归测试（见 test_watch.py）。

单独运行可以观察：
    python watch.py                跟随打印进程数变化
    python watch.py --count        只打印当前数量
    python watch.py --once-exit    阻塞直到 WorkBuddy 完全退出
"""

import sys
import time
from datetime import datetime

try:
    import _proc
except ImportError:  # 被当作包导入时
    from . import _proc

PROC = b"workbuddy.exe"


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def count_workbuddy() -> int:
    """
    当前 WorkBuddy 进程数。

    两个要点：
    1. 走 _proc.run 而不是 subprocess.run —— pythonw 下否则每 2 秒弹一次黑窗。
    2. 用 bytes 计数而不是解码：中文 Windows 的 tasklist 输出是 GBK，
       走 utf-8 解码会抛异常；进程名是 ASCII，字节匹配最稳也最快。
    """
    try:
        r = _proc.run(["tasklist", "/FO", "CSV", "/NH"],
                      capture_output=True, timeout=15)
        return r.stdout.lower().count(PROC)
    except Exception:
        return 0


class ProcessWatcher:
    """
    step() 是唯一判定入口，返回 'start' / 'exit' / None。
    follow() 只是把 step 接到回调上，测试可以直接对 step 做断言。
    """

    def __init__(self, interval=2.0, debounce=2, quiet=True, counter=None):
        self.interval = interval
        self.debounce = debounce
        self.quiet = quiet
        self.counter = counter or count_workbuddy
        self._running = None      # 是否处于「已确认运行」状态
        self._zero_streak = 0     # 连续归零次数
        self._prev = None

    def step(self):
        n = self.counter()

        if self._running is None:
            # 第一帧只建立基线，不产生边沿
            self._running = n > 0
            self._zero_streak = 0 if n > 0 else self.debounce
            self._prev = n
            return None

        event = None
        if n == 0:
            self._zero_streak += 1
            # 关键：连续归零要持续累加。之前写成只在「上一帧 >0」时才累加，
            # 导致第二帧起就不再计数，exit 永远不会触发。
            if self._zero_streak >= self.debounce and self._running:
                event = "exit"
                self._running = False
        else:
            if not self._running:
                # 只有之前已确认退出，才算是新的启动边沿。
                # 这样「归零一帧又回来」的刷新抖动不会被误判成重启。
                event = "start"
                self._running = True
            elif self._zero_streak:
                if not self.quiet:
                    log("进程短暂归零后又出现，判定为刷新而非退出")
            self._zero_streak = 0

        self._prev = n
        return event

    def wait_for_exit(self, timeout=None):
        """阻塞直到 WorkBuddy 完全退出。返回 True / 超时 False。"""
        start = time.time()
        # 先等它出现
        while self.counter() == 0:
            if timeout and time.time() - start > timeout:
                return False
            time.sleep(self.interval)
        self._running = None  # 重置基线
        while True:
            if timeout and time.time() - start > timeout:
                return False
            if self.step() == "exit":
                return True
            time.sleep(self.interval)

    def wait_for_start(self, timeout=None):
        start = time.time()
        while self.counter() == 0:
            if timeout and time.time() - start > timeout:
                return False
            time.sleep(self.interval)
        return True

    def follow(self, on_start=None, on_exit=None):
        """常驻跟踪，回调在边沿触发。Ctrl+C 退出。"""
        if not self.quiet:
            log(f"开始监听，当前 {self.counter()} 个进程")
        try:
            while True:
                ev = self.step()
                if ev == "start":
                    if not self.quiet:
                        log(f"→ WorkBuddy 启动（{self.counter()} 个进程）")
                    if on_start:
                        try:
                            on_start()
                        except Exception as ex:
                            log(f"启动回调出错：{ex}")
                elif ev == "exit":
                    if not self.quiet:
                        log("→ WorkBuddy 已退出")
                    if on_exit:
                        try:
                            on_exit()
                        except Exception as ex:
                            log(f"退出回调出错：{ex}")
                time.sleep(self.interval)
        except KeyboardInterrupt:
            if not self.quiet:
                log("监听结束")


def main():
    w = ProcessWatcher(quiet=False)

    if "--count" in sys.argv:
        print(count_workbuddy())
        return

    if "--once-exit" in sys.argv:
        log("等待 WorkBuddy 完全退出…")
        ok = w.wait_for_exit()
        log("已退出" if ok else "超时")
        return

    log("跟随模式，Ctrl+C 结束")
    w.follow(on_start=lambda: log("【回调】启动"),
             on_exit=lambda: log("【回调】退出"))


if __name__ == "__main__":
    main()
