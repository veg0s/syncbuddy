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
syncbuddy / test_watch.py — 进程边沿检测的回归测试

真实场景不好反复开关 WorkBuddy（会把自己关掉），所以注入假的进程数序列，
直接打在 ProcessWatcher.step() 这个真实判定函数上，而不是复刻一份逻辑。

    python test_watch.py
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sbwatch", ROOT / "watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def replay(seq, debounce=2):
    """按序列逐帧调用真实 step()，收集边沿事件。"""
    it = iter(seq)
    w = watch.ProcessWatcher(interval=0, debounce=debounce, quiet=True,
                             counter=lambda: next(it, seq[-1]))
    events = []
    for _ in seq:
        ev = w.step()
        if ev:
            events.append(ev)
    return events


CASES = [
    ("正常启动后退出",
     [0, 0, 8, 8, 0, 0, 0], ["start", "exit"]),
    ("刷新抖动后正常退出",
     [0, 8, 8, 0, 8, 8, 0, 0], ["start", "exit"]),
    ("单帧归零不应判定退出",
     [0, 8, 8, 0, 8, 8], ["start"]),
    ("只是启动，没退出",
     [0, 0, 8, 8, 8], ["start"]),
    ("全程没启动过",
     [0, 0, 0, 0], []),
    ("两次启动（重启）",
     [0, 8, 0, 0, 8, 0, 0], ["start", "exit", "start", "exit"]),
    ("开机时已退出（不该误报退出边沿）",
     [0, 0, 0], []),
]


def main():
    print("── 进程边沿判定（打在真实 step 上）──")
    ok = fail = 0
    for desc, seq, expect in CASES:
        got = replay(seq)
        if got == expect:
            ok += 1
            print(f"  ✔ {desc}")
        else:
            fail += 1
            print(f"  ✘ {desc}\n     序列 {seq}\n     期望 {expect}\n     实际 {got}")

    print("\n── 当前机器实际进程数 ──")
    n = watch.count_workbuddy()
    print(f"  WorkBuddy 进程数：{n}" + ("（运行中）" if n else "（未运行）"))

    print(f"\n{ok} 通过 / {fail} 失败")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
