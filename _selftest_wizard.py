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
syncbuddy / _selftest_wizard.py — 配置向导单独自检

为什么单独一个文件：向导构造时会调 subprocess（读 git remote）、
创建 QScrollArea，放进 UI 自检里偶发挂起。单独跑更稳，也更快定位。

    python _selftest_wizard.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PySide6.QtWidgets import QApplication

import setup_wizard as sw
import wizard as wz


FAKE = {
    "hostname": "test-host", "exe": "C:/fake/WorkBuddy.exe",
    "workbuddy_home": "C:/fake/.workbuddy", "uid": "0000-test",
    "workspace_root": "C:/fake/WorkBuddy", "personal_dir": "C:/fake/p",
    "uid_dir": "C:/fake/u", "storage_dir": "C:/fake/s",
    "age_key_file": "", "age_binary": "age", "python": "python",
}


def main():
    app = QApplication([])
    orig = wz.detect_all
    wz.detect_all = lambda: dict(FAKE)
    ok = fail = 0

    def check(name, fn):
        nonlocal ok, fail
        try:
            r = fn()
            print(f"  ✔ {name}" + (f"  {r}" if r else ""))
            ok += 1
        except Exception as ex:
            print(f"  ✘ {name}: {type(ex).__name__}: {ex}")
            fail += 1

    w = None

    def mk():
        nonlocal w
        w = sw.SetupWizard(Path("C:/Users/viso/WorkBuddy/工具/wb-sync"))
        assert w.page == 0
        return f"{w.width()}x{w.height()}"
    check("创建向导", mk)

    def p1():
        assert len(w.stack.count() and [1]) >= 0
        return "第 1 页"
    check("第 1 页", p1)

    def p2():
        w.go_next()
        assert w.page == 1, w.page
        # 假 hostname 不在 config.json 里 → age 私钥不该被预填。
        # SSH 私钥会去读 git core.sshCommand（那是本机已有配置），
        # 所以它可能有值，这里不做断言。
        assert w.ed_key.text() == "", "无本机配置时 age 私钥不该预填"
        return "第 2 页，age 密钥框留空"
    check("第 2 页（无配置不预填）", p2)

    def p2_saved():
        """有配置时应该回填 —— 读自己存过的值不算猜。"""
        w.ed_key.setText("")
        saved = w._saved_host()
        return f"本机段字段 {len(saved)} 个"
    check("第 2 页（能读到已保存配置）", p2_saved)

    def p3():
        # 不填 age 私钥会弹 QMessageBox 警告，无头环境下会永久阻塞，
        # 所以先塞个假路径再翻页
        w.ed_key.setText("C:/fake/workwork.key")
        w.go_next()
        assert w.page == 2, w.page
        assert len(w.type_checks) == 4, len(w.type_checks)
        return f"第 3 页，{len(w.type_checks)} 类"
    check("第 3 页", p3)

    def back():
        w.go_back()
        assert w.page == 1
        return "回退正常"
    check("回退", back)

    wz.detect_all = orig
    print(f"\n{ok} 通过 / {fail} 失败")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
