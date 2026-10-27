#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2026 Veg0s
#
"""
重新生成 README 用的预览图，输出到 docs/。

    python _preview_panel.py

全部用构造的示例数据，不读数据库、不读 git 配置 —— 否则截图里会带上
真实的会话标题、SSH 私钥路径和仓库地址，公开出去等于泄露。

界面改了之后跑一次，docs/ 里的图就同步更新了。
"""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import gui
import paths

DOCS = pathlib.Path(__file__).resolve().parent / "docs"


def _sample_plans():
    """构造一份待同步数据，标题全部用中性词。"""
    up = {"total": 5, "categories": {
        "sessions": {"label": "会话", "items": [
            {"name": "季度报表整理", "key": "a1", "detail": "更新信息+对话"},
            {"name": "接口联调记录", "key": "a2", "detail": "更新对话"},
            {"name": "会话内任务与计划（12 个文件）", "key": "__side__", "detail": "随会话同步"},
        ]},
        "config": {"label": "配置", "items": [
            {"name": "sidebar-snapshot.json", "key": "c1", "detail": ""},
            {"name": "skill-picker.json", "key": "c2", "detail": ""},
        ]},
    }}
    down = {"total": 2, "categories": {
        "sessions": {"label": "会话", "items": [
            {"name": "数据清洗脚本调试", "key": "d1", "detail": "远端新增"},
            {"name": "部署文档草稿", "key": "d2", "detail": "远端新增"},
        ]},
    }}
    return {"status": "synced", "last_push": time.time() - 420,
            "plan_ts": 1, "plans": {"upload": up, "download": down}}


def gen_panels(app, data):
    for tab, name in (("download", "panel-pull"), ("upload", "panel-push")):
        p = gui.TrayPanel(lambda d=dict(data): d, None, None, None, None)
        p.setAttribute(Qt.WA_DontShowOnScreen, True)
        if p.tab != tab:
            p._switch(tab)
        p.show()
        app.processEvents()
        out = DOCS / f"{name}.png"
        p.grab().save(str(out))
        print(f"  {out.name:18s} {p.width()}x{p.height()}")
        p.hide()


def gen_splash(app):
    s = gui.SplashWindow()
    s.setAttribute(Qt.WA_DontShowOnScreen, True)
    s.show()
    app.processEvents()
    # 停在「正在拉取」这一步，比全空或全完成更有信息量
    s.set_status("check", "done")
    s.set_status("pull", "active")
    s.set_text("pull", "拉取 2 个会话")
    app.processEvents()
    out = DOCS / "splash.png"
    s.grab().save(str(out))
    print(f"  {out.name:18s} {s.width()}x{s.height()}")
    s.hide()


def gen_wizard(app):
    import setup_wizard as sw
    import wizard as wz

    wz.detect_all = lambda: {
        "hostname": "your-pc",
        "exe": "C:/Program Files/WorkBuddy/WorkBuddy.exe",
        "workbuddy_home": "C:/Users/you/.workbuddy",
        "uid": "00000000-demo",
        "workspace_root": "C:/Users/you/WorkBuddy",
        "personal_dir": "", "uid_dir": "", "storage_dir": "",
        "age_key_file": "", "age_binary": "age", "python": "python",
    }
    # 不读真实 git 配置，否则会把本机私钥路径印到图上
    sw.SetupWizard._ssh_from_git = lambda self: ""

    w = sw.SetupWizard(paths.WB_SYNC)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.show()
    app.processEvents()

    out1 = DOCS / "wizard-1.png"
    w.grab().save(str(out1))
    print(f"  {out1.name:18s} {w.width()}x{w.height()}")

    w.go_next()
    app.processEvents()
    w.ed_ssh.setText("C:/Users/you/.ssh/id_ed25519")
    w.ed_key.setText("C:/Users/you/.config/age/workwork.key")
    w.ed_remote.setText("git@github.com:yourname/workbuddy-sync.git")
    w.ed_proxy.setText("http://127.0.0.1:7890")
    w.cb_proxy.setChecked(False)
    app.processEvents()

    out2 = DOCS / "wizard-2.png"
    w.grab().save(str(out2))
    print(f"  {out2.name:18s} {w.width()}x{w.height()}")
    w.hide()


def main():
    DOCS.mkdir(exist_ok=True)
    app = QApplication([])
    print("生成预览图到 docs/：")
    gen_panels(app, _sample_plans())
    gen_splash(app)
    gen_wizard(app)
    print(f"\n完成，{len(list(DOCS.glob('*.png')))} 张图")
    return 0


if __name__ == "__main__":
    sys.exit(main())
