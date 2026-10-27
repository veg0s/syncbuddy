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
syncbuddy / _selftest_gui.py — 离屏构建所有窗口，确认能实例化

不需要显示器，跑一遍就能发现布局参数错误、缺失导入、信号连接笔误。
打包前必跑。

    python _selftest_gui.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PySide6.QtWidgets import QApplication

import gui


def main():
    app = QApplication([])
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

    check("托盘图标绘制", lambda: f"{gui.tray_pixmap(64).width()}px")

    def mk_splash():
        s = gui.SplashWindow()
        assert len(s.steps) == 4, "应该有 4 个步骤"
        s.set_status("check", "done")
        s.set_status("pull", "active")
        s.set_text("pull", "拉取 3 个会话")
        s.set_status("write", "skip")
        s.set_status("launch", "error")
        return f"{s.width()}x{s.height()}，{len(s.steps)} 步"
    check("启动窗（分步进度）", mk_splash)

    def mk_preview():
        items = [
            {"id": "a1", "title": "临颍先打后补培训会 PPT 第二版",
             "summary": "把第二版 PPT 的第四部分改一下：猪禽牛羊技术服务那块，要突出我们做过的"
                        "非洲猪瘟检测案例，数据用 2025 年秋防的采样量。",
             "cwd": "C:/Users/other/WorkBuddy/demo", "host": "sim-pc-b",
             "updated_at": 1793000000000},
            {"id": "a2", "title": "周口川汇区布病免疫台账核对",
             "summary": "这份台账里 3 月到 5 月的免疫数量对不上，帮我按村上找出差异行。",
             "cwd": "C:/Users/other/WorkBuddy/demo", "host": "sim-pc-b",
             "updated_at": 1793000000000},
        ]
        d = gui.PreviewDialog(items, {"new_count": 2, "remote_total": 2, "local_total": 0})
        assert len(d.checks) == 2, "复选框数量不对"
        assert d.selected_ids() == ["a1", "a2"], "默认应全选"
        return f"{len(d.checks)} 项，默认全选"
    check("差异预览窗", mk_preview)

    def mk_settings():
        # 不实例化 SyncBuddy（会读数据库），直接喂假配置
        from pathlib import Path as _P
        fake_cfg = {
            "_local": {"workbuddy_home": "C:/fake/.workbuddy"},
            "proxy": {"enabled": False, "url": "http://127.0.0.1:7890"},
            "auto_sync": {"sessions": True},
        }
        d = gui.SettingsDialog(fake_cfg, _P("C:/Users/viso/WorkBuddy/工具/wb-sync"))
        cfg2, remote = d.result_config()
        assert "proxy" in cfg2 and "auto_sync" in cfg2
        return f"远程 {remote[:36]}"
    check("设置窗", mk_settings)


    def mk_panel():
        import time as _t
        now = _t.time()
        plans = {
            "upload": {"total": 5, "categories": {
                "sessions": {"label": "会话", "items": [
                    {"name": "临颍先打后补培训会 PPT 第二版"},
                    {"name": "周口川汇区布病免疫台账核对"},
                    {"name": "da9217e8…"}]},
                "config": {"label": "配置", "items": [
                    {"name": "sidebar-snapshot.json"},
                    {"name": "skill-picker.json"}]},
            }},
            "download": {"total": 2, "categories": {
                "sessions": {"label": "会话", "items": [
                    {"name": "另一台机器的任务A", "key": "a1"},
                    {"name": "任务B", "key": "a2"}]},
            }},
        }
        data = {"status": "synced", "last_push": now - 120,
                "plan_ts": 1, "plans": plans}
        p = gui.TrayPanel(lambda: data, None, None, None, None)
        p._switch("upload")          # 默认页签是待拉取，这里测待上传
        assert len(p.blocks) == 2, f"类别块数量不对：{len(p.blocks)}"
        assert p.tab_up.text().startswith("待上传 5"), p.tab_up.text()
        return f"{p.width()}x{p.height()}，{len(p.blocks)} 类"
    check("托盘面板（待上传）", mk_panel)

    def mk_panel_down():
        import time as _t
        plans = {
            "upload": {"total": 0, "categories": {}},
            "download": {"total": 2, "categories": {
                "sessions": {"label": "会话", "items": [
                    {"name": "另一台机器的任务A", "key": "a1"},
                    {"name": "任务B", "key": "a2"}]},
            }},
        }
        data = {"status": "synced", "last_push": _t.time() - 60,
                "plan_ts": 1, "plans": plans}
        p = gui.TrayPanel(lambda: data, None, None, None, None)
        p._switch("download")
        blk = p.blocks.get("sessions")
        assert blk is not None and len(blk.item_checks) == 2, "拉取页签会话应可逐条勾选"
        return f"{p.width()}x{p.height()}，2 条可勾选"
    check("托盘面板（待拉取）", mk_panel_down)

    def mk_history():
        import time as _t
        now = _t.time()
        evs = [
            {"ts": now - 30, "kind": "pull", "title": "临颍先打后补培训会 PPT 第二版",
             "detail": "已拉取到本机"},
            {"ts": now - 300, "kind": "push", "title": "", "detail": "已上传 4 项改动"},
            {"ts": now - 4000, "kind": "error", "title": "",
             "detail": "hostkeys_foreach failed for known_hosts: Permission denied"},
        ]
        d = gui.HistoryDialog(evs)
        return f"{d.width()}x{d.height()}，3 条"
    check("同步历史窗", mk_history)

    def mk_panel_empty():
        data = {"status": "idle", "plans": {"upload": {"total": 0, "categories": {}},
                                            "download": {"total": 0, "categories": {}}}}
        p = gui.TrayPanel(lambda: data, None, None, None, None)
        return f"{p.width()}x{p.height()}，空状态"
    check("托盘面板（空）", mk_panel_empty)

    print(f"\n{ok} 通过 / {fail} 失败")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
