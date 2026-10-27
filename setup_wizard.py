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
syncbuddy / setup_wizard.py — 首次运行引导

三页：
  1. 环境检测   自动探测 exe / 数据目录 / 工作区根 / 私钥，缺什么一眼看到
  2. 连接配置   私钥路径、仓库地址、代理（都带测试）
  3. 同步类型   默认自动同步哪几类

写回的是仓库里的 config.json：hosts 段、proxy、auto_sync。
"""

import json
import os
import paths
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

ACCENT = "#0F6E56"
TEXT = "#2C2C2A"
MUTED = "#5F5E5A"
BORDER = "#E3E1DA"
BG = "#FFFFFF"
BG_SOFT = "#F7F6F2"
OK_C = "#0F6E56"
ERR_C = "#E24B4A"
WARN_C = "#BA7517"

QSS = f"""
QWidget {{ font-family:"Microsoft YaHei UI","Segoe UI",sans-serif; font-size:13px; color:{TEXT}; }}
QDialog {{ background:{BG}; }}
QLabel#h1 {{ font-size:17px; font-weight:500; }}
QLabel#h2 {{ font-size:13px; font-weight:500; }}
QLabel#muted {{ color:{MUTED}; font-size:12px; }}
QPushButton {{ background:{ACCENT}; color:white; border:none; border-radius:8px;
               padding:8px 20px; font-size:13px; }}
QPushButton:hover {{ background:#085041; }}
QPushButton:disabled {{ background:#C9C7C0; }}
QPushButton#ghost {{ background:transparent; color:{TEXT}; border:1px solid {BORDER}; }}
QPushButton#ghost:hover {{ background:{BG_SOFT}; }}
QPushButton#ghost:disabled {{ color:#BFBDB5; border-color:#EDEBE4; }}
QPushButton#ghost:disabled {{ color:#BFBDB5; border-color:#EDEBE4; }}
QLineEdit {{ border:1px solid {BORDER}; border-radius:8px; padding:7px 10px; background:{BG}; }}
QLineEdit:focus {{ border:1px solid {ACCENT}; }}
QCheckBox {{ spacing:8px; }}
QFrame#card {{ background:{BG_SOFT}; border:1px solid {BORDER}; border-radius:10px; }}
"""


def _row(label, value, ok=True, note=""):
    """一行「标签 : 值 (状态)」"""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 2, 0, 2)
    h.setSpacing(8)

    k = QLabel(label)
    k.setFixedWidth(78)
    k.setStyleSheet(f"color:{MUTED};font-size:12px;")
    h.addWidget(k)

    v = QLabel(value or "（未找到）")
    v.setWordWrap(True)
    v.setStyleSheet(f"color:{TEXT if ok else ERR_C};font-size:12px;")
    h.addWidget(v, 1)

    if note:
        n = QLabel(note)
        n.setStyleSheet(f"color:{OK_C if ok else ERR_C};font-size:12px;")
        h.addWidget(n)
    return w


class SetupWizard(QDialog):
    def __init__(self, wb_sync: Path, parent=None):
        super().__init__(parent)
        self.wb_sync = Path(wb_sync)
        self.cfg_file = self.wb_sync / "config.json"
        self.page = 0
        self.detect = {}
        self.ok = False

        self.setWindowTitle("SyncBuddy 首次配置")
        self.setFixedSize(640, 600)      # 固定尺寸，避免拖拽时抖动
        self.setStyleSheet(QSS)

        # 先探测：第 2 页的输入框拿探测结果当默认值，
        # 顺序反了的话私钥框会是空的（踩过）
        self._detect()

        # 先探测：第 2 页的输入框拿探测结果当默认值，
        # 顺序反了的话私钥框会是空的（踩过）
        self._detect()

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(12)

        # ── 顶部 ──
        self.title = QLabel("环境检测")
        self.title.setObjectName("h1")
        root.addWidget(self.title)

        self.subtitle = QLabel("先看看本机的情况")
        self.subtitle.setObjectName("muted")
        root.addWidget(self.subtitle)

        self.steps_lbl = QLabel("1 / 3")
        self.steps_lbl.setStyleSheet(f"color:{MUTED};font-size:12px;")
        root.addWidget(self.steps_lbl)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{BORDER};border:none;")
        root.addWidget(line)

        # ── 主体 ──
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._page_env())
        self.stack.addWidget(self._page_conn())
        self.stack.addWidget(self._page_types())

        # ── 底部按钮 ──
        btns = QHBoxLayout()
        self.btn_back = QPushButton("上一步")
        self.btn_back.setObjectName("ghost")
        self.btn_back.clicked.connect(self.go_back)
        self.btn_back.setEnabled(False)
        btns.addWidget(self.btn_back)
        btns.addStretch()
        self.btn_next = QPushButton("下一步")
        self.btn_next.clicked.connect(self.go_next)
        btns.addWidget(self.btn_next)
        root.addLayout(btns)

        self.refresh_env()

    # ────────────────── 第 1 页：环境检测 ──────────────────

    def _page_env(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(4)

        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.setSpacing(2)

        self.env_box = QVBoxLayout()
        self.env_box.setSpacing(2)
        cl.addLayout(self.env_box)
        v.addWidget(card)

        self.env_hint = QLabel("")
        self.env_hint.setWordWrap(True)
        self.env_hint.setStyleSheet(f"color:{WARN_C};font-size:12px;")
        v.addWidget(self.env_hint)

        btn = QPushButton("重新检测")
        btn.setObjectName("ghost")
        btn.clicked.connect(self.refresh_env)
        v.addWidget(btn, 0, Qt.AlignLeft)

        v.addStretch()
        return w

    def _detect(self):
        try:
            import wizard as wz
            self.detect = wz.detect_all()
        except Exception as ex:
            self.detect = {"_error": str(ex)}

    def refresh_env(self):
        self._detect()

        while self.env_box.count():
            it = self.env_box.takeAt(0)
            if it.widget():
                it.widget().setParent(None)

        d = self.detect
        rows = [
            ("主机名", d.get("hostname"), bool(d.get("hostname")), ""),
            ("WorkBuddy", d.get("exe"), bool(d.get("exe")), "已找到" if d.get("exe") else "未找到"),
            ("数据目录", d.get("workbuddy_home"), Path(d.get("workbuddy_home") or "").is_dir(), ""),
            ("用户 UID", d.get("uid"), bool(d.get("uid")), ""),
            ("工作区根", d.get("workspace_root"), bool(d.get("workspace_root")), ""),
        ]
        missing = []
        for k, val, ok, note in rows:
            self.env_box.addWidget(_row(k, val or "", ok, note))
            if not ok:
                missing.append(k)

        if missing:
            self.env_hint.setText(
                f"⚠ {('、'.join(missing))} 没找到。可以继续，下一页能手动填；"
                f"但缺少私钥会导致记忆无法解密。")
        else:
            self.env_hint.setText("")
        self.env_hint.setStyleSheet(
            f"color:{WARN_C if missing else OK_C};font-size:12px;")

        # 重新检测后把私钥补进第 2 页（用户没手动填过才补）
        ed = getattr(self, "ed_key", None)
        if ed is not None and not ed.text().strip():
            ed.setText(d.get("age_key_file") or "")

        # 重新检测后把私钥补进第 2 页（用户没手动填过才补）
        ed = getattr(self, "ed_key", None)
        if ed is not None and not ed.text().strip():
            ed.setText(d.get("age_key_file") or "")

    # ────────────────── 第 2 页：连接配置 ──────────────────

    def _saved_host(self):
        """读 config.json 里本机那一段 —— 自己存过的值，不是猜出来的。"""
        try:
            cfg = json.loads(self.cfg_file.read_text(encoding="utf-8"))
            h = self.detect.get("hostname") or ""
            return (cfg.get("hosts") or {}).get(h) or {}
        except Exception:
            return {}

    def _ssh_from_git(self):
        """老版本只把 SSH 私钥写进了 git core.sshCommand，这里取出来回填。"""
        try:
            import subprocess as _sp
            r = _sp.run(["git", "config", "--get", "core.sshCommand"],
                        cwd=self.wb_sync, capture_output=True, text=True,
                        timeout=15, creationflags=0x08000000)
            m = re.search(r'-i\s+"?([^"]+)"?', (r.stdout or "").strip())
            return m.group(1) if m else ""
        except Exception:
            return ""

    def _page_conn(self):
        saved = self._saved_host()
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(10)

        # 数据仓库在哪 —— 解压版用户不知道程序把东西存哪去了
        repo_exists = self.wb_sync.is_dir()
        lb_repo = QLabel(
            "数据仓库：" + str(self.wb_sync) +
            ("" if repo_exists else "（尚未创建，完成后会自动建目录）"))
        lb_repo.setWordWrap(True)
        lb_repo.setStyleSheet(f"color:{MUTED};font-size:11px;")
        v.addWidget(lb_repo)

        # SSH 私钥：由用户自己指定绝对路径，程序不猜
        v.addWidget(self._h2("SSH 私钥 · 访问仓库（可选）"))
        r0 = QHBoxLayout()
        self.ed_ssh = QLineEdit(saved.get("ssh_key_file") or self._ssh_from_git())
        self.ed_ssh.setFixedHeight(32)
        self.ed_ssh.setPlaceholderText(r"留空则使用系统默认的 ~/.ssh/config")
        r0.addWidget(self.ed_ssh, 1)
        b0 = QPushButton("浏览")
        b0.setObjectName("ghost")
        b0.clicked.connect(lambda: self._pick_into(self.ed_ssh, "选择 SSH 私钥"))
        r0.addWidget(b0)
        v.addLayout(r0)
        v.addWidget(self._muted("填了就用这把钥匙（会写入 git 的 core.sshCommand）；"
                                "留空则沿用系统 SSH 配置。"))

        # age 私钥：解密 memory
        v.addWidget(self._h2("age 私钥 · 解密记忆"))
        r1 = QHBoxLayout()
        self.ed_key = QLineEdit(saved.get("age_key_file") or "")
        self.ed_key.setFixedHeight(32)
        self.ed_key.setPlaceholderText("请填写绝对路径，例如 D:\\keys\\workwork.key")
        r1.addWidget(self.ed_key, 1)
        b1 = QPushButton("浏览")
        b1.setObjectName("ghost")
        b1.clicked.connect(self._pick_key)
        r1.addWidget(b1)
        v.addLayout(r1)
        v.addWidget(self._muted("只用来解密 memory，和 SSH 私钥不是同一个东西。"))

        # 仓库
        v.addWidget(self._h2("远程仓库"))
        r2 = QHBoxLayout()
        self.ed_remote = QLineEdit(self._current_remote())
        self.ed_remote.setFixedHeight(32)
        self.ed_remote.setPlaceholderText("git@主机:用户名/仓库.git")
        r2.addWidget(self.ed_remote, 1)
        b2 = QPushButton("测试")
        b2.setObjectName("ghost")
        b2.clicked.connect(self._test_remote)
        r2.addWidget(b2)
        v.addLayout(r2)
        self.remote_out = QLabel("")
        self.remote_out.setStyleSheet(f"color:{MUTED};font-size:12px;")
        self.remote_out.setFixedHeight(20)
        v.addWidget(self.remote_out)

        # 代理
        v.addWidget(self._h2("代理（可选）"))
        px = {}
        try:
            px = json.loads(self.cfg_file.read_text(encoding="utf-8")).get("proxy") or {}
        except Exception:
            pass
        r3 = QHBoxLayout()
        self.cb_proxy = QCheckBox("启用")
        self.cb_proxy.setChecked(bool(px.get("enabled")))
        r3.addWidget(self.cb_proxy)
        self.ed_proxy = QLineEdit(px.get("url") or "http://127.0.0.1:7890")
        self.ed_proxy.setFixedHeight(32)
        self.ed_proxy.setFixedHeight(32)
        r3.addWidget(self.ed_proxy, 1)
        b3 = QPushButton("测试")
        b3.setObjectName("ghost")
        b3.clicked.connect(self._test_proxy)
        r3.addWidget(b3)
        v.addLayout(r3)
        self.proxy_out = QLabel("")
        self.proxy_out.setStyleSheet(f"color:{MUTED};font-size:12px;")
        self.proxy_out.setFixedHeight(20)
        v.addWidget(self.proxy_out)

        v.addStretch()
        # 内容比一屏多，套滚动区，避免控件被压扁
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        scroll.setWidget(w)
        return scroll

    def _pick_into(self, editor, title):
        f, _ = QFileDialog.getOpenFileName(self, title, "", "所有文件 (*)")
        if f:
            editor.setText(f)

    def _check_ssh(self):
        pass          # 不自动探测私钥位置，一律由用户指定

    def _pick_key(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 age 私钥", "", "密钥文件 (*.key *);;所有文件 (*)")
        if f:
            self.ed_key.setText(f)

    def _current_remote(self):
        # config.json 里存过就直接用 —— 那是用户上次填的，比读 git 可靠
        try:
            cfg = json.loads(self.cfg_file.read_text(encoding="utf-8"))
            saved = (cfg.get("remote") or "").strip()
            if saved:
                return saved
        except Exception:
            pass
        try:
            r = subprocess.run(["git", "remote", "get-url", "origin"],
                               cwd=self.wb_sync, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=15, creationflags=0x08000000)
            return (r.stdout or "").strip()
        except Exception:
            return ""

    def _test_remote(self):
        url = self.ed_remote.text().strip()
        if not url:
            self.remote_out.setText("先填地址")
            return
        self.remote_out.setText("测试中…")
        self.remote_out.repaint()
        try:
            r = subprocess.run(["git", "ls-remote", "--heads", url],
                               capture_output=True, timeout=25,
                               creationflags=0x08000000,
                               env=self._git_env())
            if r.returncode == 0:
                n = len([l for l in r.stdout.decode("utf-8", "replace").splitlines() if l])
                self.remote_out.setText(f"✔ 连接正常，{n} 个分支")
                self.remote_out.setStyleSheet(f"color:{OK_C};font-size:12px;")
            else:
                err = (r.stderr or b"").decode("utf-8", "replace").strip()[:80]
                self.remote_out.setText(f"✘ {err}")
                self.remote_out.setStyleSheet(f"color:{ERR_C};font-size:12px;")
        except Exception as ex:
            self.remote_out.setText(f"✘ {str(ex)[:80]}")
            self.remote_out.setStyleSheet(f"color:{ERR_C};font-size:12px;")

    def _git_env(self):
        import os
        env = os.environ.copy()
        if self.cb_proxy.isChecked():
            url = self.ed_proxy.text().strip().rstrip("/")
            hostport = url.split("://", 1)[-1]
            env["GIT_SSH_COMMAND"] = (
                f'ssh -o ProxyCommand="C:/PROGRA~1/Git/mingw64/bin/connect.exe '
                f'-H {hostport} %h %p"')
        return env

    def _test_proxy(self):
        url = self.ed_proxy.text().strip()
        if not url:
            self.proxy_out.setText("先填地址")
            return
        # 测的是你填的远程仓库主机，不绑定任何一家平台
        host = paths.remote_host(self.ed_remote.text().strip())
        if not host:
            self.proxy_out.setText("先在上面填远程仓库地址")
            self.proxy_out.setStyleSheet(f"color:{WARN_C};font-size:12px;")
            return
        self.proxy_out.setText("测试中…")
        self.proxy_out.repaint()
        t0 = time.time()
        try:
            op = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": url, "https": url}))
            with op.open(f"https://{host}", timeout=8) as r:
                ms = int((time.time() - t0) * 1000)
                self.proxy_out.setText(f"✔ 连通 {host}（{r.status}，{ms}ms）")
                self.proxy_out.setStyleSheet(f"color:{OK_C};font-size:12px;")
        except Exception as ex:
            self.proxy_out.setText(f"✘ {host} 连不上：{str(ex)[:60]}")
            self.proxy_out.setStyleSheet(f"color:{ERR_C};font-size:12px;")

    # ────────────────── 第 3 页：同步类型 ──────────────────

    def _page_types(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(8)

        v.addWidget(self._muted("默认自动同步哪些内容。以后可以在设置里改。"))

        auto = {}
        try:
            auto = json.loads(self.cfg_file.read_text(encoding="utf-8")).get("auto_sync") or {}
        except Exception:
            pass

        self.type_checks = {}
        for key, label, desc in (
            ("sessions", "会话", "任务列表、对话正文，以及会话内的任务清单与计划"),
            ("identity", "身份与记忆", "SOUL / IDENTITY / USER / MEMORY 和长期记忆（age 加密）"),
            ("skills", "技能", "skills 目录下的全部技能文件"),
            ("config", "配置", "mcp 配置、侧边栏快照、置顶状态、定时任务定义"),
        ):
            card = QFrame()
            card.setObjectName("card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 10, 14, 10)
            cl.setSpacing(2)

            cb = QCheckBox(label)
            cb.setChecked(bool(auto.get(key, True)))
            cl.addWidget(cb)

            d = QLabel(desc)
            d.setObjectName("muted")
            d.setWordWrap(True)
            cl.addWidget(d)

            v.addWidget(card)
            self.type_checks[key] = cb

        v.addStretch()
        return w

    # ────────────────── 导航 ──────────────────

    def _h2(self, text):
        lb = QLabel(text)
        lb.setObjectName("h2")
        return lb

    def _muted(self, text):
        lb = QLabel(text)
        lb.setObjectName("muted")
        lb.setWordWrap(True)
        return lb

    def go_next(self):
        if self.page == 0:
            self.page = 1
            self.title.setText("连接配置")
            self.subtitle.setText("私钥、仓库、代理")
        elif self.page == 1:
            if not self.ed_key.text().strip():
                QMessageBox.warning(self, "还差一步",
                                    "私钥没填的话记忆解不开。\n"
                                    "确定要继续吗？\n\n（可以先完成，之后在设置里补）")
            self.page = 2
            self.title.setText("同步类型")
            self.subtitle.setText("选默认自动同步的内容")
        else:
            self.finish()
            return
        self._sync_ui()

    def go_back(self):
        if self.page > 0:
            self.page -= 1
            self.title.setText(["环境检测", "连接配置", "同步类型"][self.page])
            self.subtitle.setText(["先看看本机的情况", "私钥、仓库、代理",
                                   "选默认自动同步的内容"][self.page])
            self._sync_ui()

    def _sync_ui(self):
        self.stack.setCurrentIndex(self.page)
        self.steps_lbl.setText(f"{self.page + 1} / 3")
        self.btn_back.setEnabled(self.page > 0)
        self.btn_next.setText("完成" if self.page == 2 else "下一步")

    # ────────────────── 写入 ──────────────────

    def finish(self):
        try:
            # 解压版第一次运行时数据仓库还不存在（那是用户自己的私有仓库），
            # 不先建目录，下面写 config.json 会直接 Errno 2。
            if not self.wb_sync.is_dir():
                self.wb_sync.mkdir(parents=True, exist_ok=True)
                if not (self.wb_sync / ".git").exists():
                    subprocess.run(["git", "init", "-q", "-b", "main"],
                                   cwd=self.wb_sync, capture_output=True,
                                   timeout=30, creationflags=0x08000000)

            cfg = {}
            if self.cfg_file.is_file():
                cfg = json.loads(self.cfg_file.read_text(encoding="utf-8"))

            host = self.detect.get("hostname") or ""
            if not host:
                QMessageBox.critical(self, "无法保存", "拿不到主机名，请点「重新检测」")
                return

            hosts = cfg.get("hosts") or {}
            hosts[host] = {
                "exe": self.detect.get("exe", ""),
                "workspace_root": self.detect.get("workspace_root", ""),
                "workbuddy_home": self.detect.get("workbuddy_home", ""),
                "personal_dir": self.detect.get("personal_dir", ""),
                "uid_dir": self.detect.get("uid_dir", ""),
                "storage_dir": self.detect.get("storage_dir", ""),
                "age_key_file": self.ed_key.text().strip(),
                "ssh_key_file": self.ed_ssh.text().strip(),
                "age_binary": self.detect.get("age_binary", "age"),
                "python": self.detect.get("python", ""),
            }
            cfg["hosts"] = hosts
            p_url, p_note = paths.normalize_proxy(self.ed_proxy.text())
            if p_note:
                self.proxy_out.setText("✔ " + p_note)
                self.ed_proxy.setText(p_url)
            cfg["proxy"] = {"enabled": self.cb_proxy.isChecked(), "url": p_url}
            cfg["auto_sync"] = {k: cb.isChecked() for k, cb in self.type_checks.items()}
            cfg.setdefault("version", 1)
            # 即使这次没填密钥，字段也要存在 —— 空的 config.json 结构不完整，
            # 后续读 cfg["age_public_key"] 的地方容易踩空
            cfg.setdefault("age_public_key", "")
            cfg.setdefault("exclude_background_automation", True)
            cfg.setdefault("sync_all_sessions", True)

            # 远程地址必须先落进 config 再写盘。之前这段在 write_text 后面，
            # 配置文件里永远没有 remote，重跑向导时输入框是空的。
            remote = self.ed_remote.text().strip()
            if remote:
                cfg["remote"] = remote
                # 刚 git init 的仓库没有 origin，set-url 会直接失败，得用 add
                r = subprocess.run(["git", "remote", "set-url", "origin", remote],
                                   cwd=self.wb_sync, capture_output=True, timeout=20,
                                   creationflags=0x08000000)
                if r.returncode != 0:
                    subprocess.run(["git", "remote", "add", "origin", remote],
                                   cwd=self.wb_sync, capture_output=True, timeout=20,
                                   creationflags=0x08000000)

            self.cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

            # 用户指定了 SSH 私钥就写进 git 配置；没指定就清掉旧的，回到系统默认
            ssh_key = self.ed_ssh.text().strip()
            if ssh_key:
                subprocess.run(
                    ["git", "config", "core.sshCommand",
                     f'ssh -i "{ssh_key}" -o IdentitiesOnly=yes'],
                    cwd=self.wb_sync, capture_output=True, timeout=20,
                    creationflags=0x08000000)
            else:
                subprocess.run(["git", "config", "--unset", "core.sshCommand"],
                               cwd=self.wb_sync, capture_output=True, timeout=20,
                               creationflags=0x08000000)

            self.ok = True
            self.accept()
        except Exception as ex:
            QMessageBox.critical(self, "保存失败", str(ex)[:300])
