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
syncbuddy / gui.py — 界面层

四个窗口：
    SplashWindow    启动时的检查进度小窗，无边框、居中、带一点动画
    PreviewDialog   差异预览，逐条勾选哪些要拉
    SettingsDialog  代理与远程设置
    Tray           托盘图标与菜单

配色走浅色 + 一点绿，避免原生控件那股塑料感。
"""

import _proc
import paths
import time
from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QStyle, QSystemTrayIcon, QVBoxLayout, QWidget,
)

ACCENT = "#0F6E56"
ACCENT_HOVER = "#085041"
TEXT = "#2C2C2A"
MUTED = "#5F5E5A"
BORDER = "#E3E1DA"
BG = "#FFFFFF"
BG_SOFT = "#F7F6F2"

QSS = f"""
QWidget {{
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QDialog, QWidget#card {{
    background: {BG};
}}
QLabel#title {{
    font-size: 15px;
    font-weight: 500;
}}
QLabel#muted {{
    color: {MUTED};
    font-size: 12px;
}}
QPushButton {{
    background: {ACCENT};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
    font-size: 13px;
}}
QPushButton:hover {{ background: {ACCENT_HOVER}; }}
QPushButton:disabled {{ background: #C9C7C0; }}
QPushButton#ghost {{
    background: transparent;
    color: {TEXT};
    border: 1px solid {BORDER};
}}
QPushButton#ghost:hover {{ background: {BG_SOFT}; }}
QCheckBox {{ spacing: 8px; }}
QLineEdit {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
    background: {BG};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QScrollArea {{ border: none; background: transparent; }}
QFrame#item {{
    background: {BG_SOFT};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QProgressBar {{
    border: none;
    background: {BG_SOFT};
    border-radius: 3px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}
"""


STATUS_COLOR = {
    "idle":      "#888780",
    "syncing":   "#378ADD",
    "uploading": "#378ADD",
    "synced":    ACCENT,
    "error":     "#E24B4A",
}

STATUS_SYMBOL = {
    "idle":      "↔",
    "syncing":   "…",
    "uploading": "↑",
    "synced":    "✓",
    "error":     "!",
}


def tray_pixmap(size=64, status="idle"):
    """
    托盘图标：圆角方块 + 状态符号。
    颜色和符号都跟状态走，这样不用打开面板也能看出当前在干嘛。
    """
    color = STATUS_COLOR.get(status, STATUS_COLOR["idle"])
    sym = STATUS_SYMBOL.get(status, "↔")

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    r = max(3, int(size * 0.24))
    p.drawRoundedRect(1, 1, size - 2, size - 2, r, r)

    p.setPen(QColor("white"))
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(max(10, int(size * 0.58)))
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, sym)
    p.end()
    return pm


class SplashWindow(QWidget):
    """
    启动时的进度窗：分步骤显示同步进行到哪一步。

    比一行"正在检查远程"有意义——用户能看出卡在哪，
    也能确认「确实在干活」而不是装死。
    """

    DOT = {"pending": "○", "active": "●", "done": "✓", "error": "!", "skip": "–"}
    COLOR = {"pending": "#B4B2A9", "active": "#378ADD",
             "done": ACCENT, "error": "#E24B4A", "skip": "#B4B2A9"}

    def __init__(self, steps=None, title="SyncBuddy"):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(QSS)

        self.steps = list(steps or [
            ("check", "检查远程更新"),
            ("pull", "拉取远程数据"),
            ("write", "写入本机"),
            ("launch", "启动 WorkBuddy"),
        ])
        self.rows = {}
        self._status = {}

        h = 108 + len(self.steps) * 26
        self.setFixedSize(380, h)

        wrap = QFrame(self)
        wrap.setObjectName("card")
        wrap.setGeometry(0, 0, 380, h)
        wrap.setStyleSheet(f"QFrame#card{{background:{BG};border:1px solid {BORDER};"
                           f"border-radius:12px;}}")

        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(tray_pixmap(26, "syncing"))
        icon.setFixedSize(26, 26)
        head.addWidget(icon)
        t = QLabel(title)
        t.setStyleSheet("font-size:15px;font-weight:500;")
        head.addWidget(t)
        head.addStretch()
        lay.addLayout(head)
        lay.addSpacing(8)

        for key, label in self.steps:
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel(self.DOT["pending"])
            dot.setFixedWidth(14)
            dot.setStyleSheet(f"color:{self.COLOR['pending']};font-size:12px;")
            row.addWidget(dot)

            txt = QLabel(label)
            txt.setStyleSheet(f"color:{MUTED};font-size:12px;")
            row.addWidget(txt)
            row.addStretch()
            self.rows[key] = (dot, txt)
            self._status[key] = "pending"
            lay.addLayout(row)

        lay.addSpacing(8)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedHeight(4)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)

        self.center()

    # ── 状态控制 ──
    def set_status(self, key, status):
        """status: pending / active / done / error / skip"""
        if key not in self.rows:
            return
        self._status[key] = status
        dot, txt = self.rows[key]
        c = self.COLOR.get(status, self.COLOR["pending"])
        dot.setText(self.DOT.get(status, "○"))
        dot.setStyleSheet(f"color:{c};font-size:12px;")
        if status == "active":
            txt.setStyleSheet(f"color:{TEXT};font-size:12px;font-weight:500;")
        elif status == "done":
            txt.setStyleSheet(f"color:{MUTED};font-size:12px;")
        else:
            txt.setStyleSheet(f"color:{MUTED};font-size:12px;")
        self._refresh_bar()

    def set_text(self, key, text):
        if key in self.rows:
            self.rows[key][1].setText(text)

    def _refresh_bar(self):
        done = sum(1 for v in self._status.values() if v in ("done", "skip"))
        total = max(1, len(self._status))
        self.bar.setValue(int(done * 100 / total))

    def finish(self, delay_close=350):
        for k in self._status:
            if self._status[k] in ("pending", "active"):
                self.set_status(k, "done")
        self.bar.setValue(100)
        QTimer.singleShot(delay_close, self.close)

    def center(self):
        scr = self.screen().availableGeometry() if self.screen() else None
        if scr:
            self.move((scr.width() - self.width()) // 2,
                      (scr.height() - self.height()) // 2)


class PreviewDialog(QDialog):
    """
    差异预览。每条一个复选框，默认全选。
    取消勾选的会话不会被拉取（对应 git checkout 时跳过那些文件）。
    """

    def __init__(self, items, stat, parent=None):
        super().__init__(parent)
        self.setWindowTitle("另一台机器有新任务")
        self.setMinimumSize(620, 480)
        self.setStyleSheet(QSS)
        self.checks = []

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        head = QLabel(f"仓库比本机多 {stat['new_count']} 个会话")
        head.setObjectName("title")
        root.addWidget(head)

        sub = QLabel("勾选的会被拉取。取消勾选的就先留在远端，下次再说。")
        sub.setObjectName("muted")
        root.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 6, 0)
        v.setSpacing(8)

        for it in items:
            card = QFrame()
            card.setObjectName("item")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 12, 14, 12)
            cl.setSpacing(6)

            cb = QCheckBox(it["title"] or "（无标题）")
            cb.setChecked(True)
            cb.setStyleSheet("QCheckBox { font-weight: 500; }")
            cl.addWidget(cb)

            body = QLabel(it["summary"])
            body.setObjectName("muted")
            body.setWordWrap(True)
            body.setMaximumHeight(60)
            cl.addWidget(body)

            meta = QLabel(f"{it.get('host', '') or '未知来源'}"
                          + (f"  ·  {Path(it['cwd']).name}" if it.get("cwd") else ""))
            meta.setStyleSheet("color:#888780; font-size:11px;")
            cl.addWidget(meta)

            v.addWidget(card)
            self.checks.append((cb, it["id"]))

        v.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        btns = QHBoxLayout()
        all_btn = QPushButton("全选")
        all_btn.setObjectName("ghost")
        all_btn.clicked.connect(lambda: [c.setChecked(True) for c, _ in self.checks])
        none_btn = QPushButton("全不选")
        none_btn.setObjectName("ghost")
        none_btn.clicked.connect(lambda: [c.setChecked(False) for c, _ in self.checks])
        btns.addWidget(all_btn)
        btns.addWidget(none_btn)
        btns.addStretch()

        skip = QPushButton("本次跳过")
        skip.setObjectName("ghost")
        skip.clicked.connect(self.reject)
        ok = QPushButton("拉取所选")
        ok.clicked.connect(self.accept)
        btns.addWidget(skip)
        btns.addWidget(ok)
        root.addLayout(btns)

    def selected_ids(self):
        return [sid for cb, sid in self.checks if cb.isChecked()]


class SettingsDialog(QDialog):
    """代理与远程地址。字段不多，一次显示完。"""

    def __init__(self, cfg, repo, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.repo = repo
        self.wants_setup = False
        self.setWindowTitle("设置")
        self.setFixedSize(520, 560)
        self.setStyleSheet(QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(14)

        t = QLabel("网络")
        t.setObjectName("title")
        root.addWidget(t)

        px = cfg.get("proxy") or {}
        self.px_on = QCheckBox("使用 HTTP(S) 代理")
        self.px_on.setChecked(bool(px.get("enabled")))
        root.addWidget(self.px_on)

        row = QHBoxLayout()
        row.addWidget(QLabel("地址"))
        self.px_url = QLineEdit(px.get("url") or "http://127.0.0.1:7890")
        row.addWidget(self.px_url, 1)
        self.test_btn = QPushButton("测试")
        self.test_btn.setObjectName("ghost")
        self.test_btn.clicked.connect(self.test_proxy)
        row.addWidget(self.test_btn)
        root.addLayout(row)

        self.test_out = QLabel("")
        self.test_out.setObjectName("muted")
        self.test_out.setWordWrap(True)
        # 固定两行高度：测试结果文字长短不一，变一次就把下面的控件整体顶一下，
        # 看起来就是"拖动时抖动"
        self.test_out.setFixedHeight(34)
        self.test_out.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        root.addWidget(self.test_out)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color:{BORDER};")
        root.addWidget(line)

        t2 = QLabel("远程仓库")
        t2.setObjectName("title")
        root.addWidget(t2)

        self.remote = QLineEdit(self._current_remote())
        root.addWidget(self.remote)

        hint = QLabel("当前是 SSH 时，代理通过 Git 自带的 connect.exe 建隧道；"
                      "HTTPS 远程则直接用 http.proxy。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setStyleSheet(f"color:{BORDER};")
        root.addWidget(line2)

        # ── 默认自动同步的类别 ──
        t3 = QLabel("默认自动同步")
        t3.setObjectName("title")
        root.addWidget(t3)

        auto = cfg.get("auto_sync") or {}
        self.cat_checks = {}
        row1 = QHBoxLayout()
        row2 = QHBoxLayout()
        layout_map = {"sessions": row1, "identity": row1, "skills": row2, "config": row2}
        for key, label in (("sessions", "会话（含会话内任务）"), ("identity", "身份与记忆"),
                           ("skills", "技能"), ("config", "配置（含定时任务）")):
            cb = QCheckBox(label)
            cb.setChecked(bool(auto.get(key, True)))
            self.cat_checks[key] = cb
            layout_map[key].addWidget(cb)
        row1.addStretch()
        row2.addStretch()
        root.addLayout(row1)
        root.addLayout(row2)

        hint2 = QLabel("关掉的类别完全不参与同步：既不推送也不拉取。")
        hint2.setObjectName("muted")
        hint2.setWordWrap(True)
        root.addWidget(hint2)

        line3 = QFrame()
        line3.setFrameShape(QFrame.HLine)
        line3.setStyleSheet(f"color:{BORDER};")
        root.addWidget(line3)

        log_row = QHBoxLayout()
        log_btn = QPushButton("查看日志")
        log_btn.setObjectName("ghost")
        log_btn.clicked.connect(self._open_log)
        log_row.addWidget(log_btn)

        setup_btn = QPushButton("重新运行配置向导")
        setup_btn.setObjectName("ghost")
        setup_btn.clicked.connect(self._run_setup)
        log_row.addWidget(setup_btn)
        log_row.addStretch()
        root.addLayout(log_row)

        root.addStretch()
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

    def _current_remote(self):
        r = _proc.run(["git", "remote", "get-url", "origin"], cwd=self.repo,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return (r.stdout or "").strip()

    def test_proxy(self):
        import urllib.request
        import time

        url = self.px_url.text().strip()
        # 测远程仓库所在主机，而不是写死 github.com ——
        # 换 Gitee / GitLab / 自建 Gitea 时才有参考意义
        host = paths.remote_host(self._current_remote())
        if not host:
            self.test_out.setText("✘ 还没配置远程仓库地址")
            return
        self.test_out.setText("测试中…")
        self.test_btn.setEnabled(False)
        self.repaint()
        t0 = time.time()
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": url, "https": url}))
            with opener.open(f"https://{host}", timeout=8) as r:
                ms = int((time.time() - t0) * 1000)
                self.test_out.setText(f"✔ 经代理连通 {host}（HTTP {r.status}，{ms}ms）")
        except Exception as ex:
            self.test_out.setText(f"✘ {host} 连不上：{str(ex)[:70]}")
        finally:
            self.test_btn.setEnabled(True)

    def _run_setup(self):
        self.wants_setup = True
        self.accept()          # 交给主程序去弹向导

    def _open_log(self):
        d = paths.APP_DIR / "logs"
        d.mkdir(exist_ok=True)
        try:
            _proc.popen(["explorer", str(d)])
        except Exception:
            pass

    def result_config(self):
        cfg = dict(self.cfg)
        cfg["proxy"] = {"enabled": self.px_on.isChecked(),
                        "url": self.px_url.text().strip()}
        cfg["auto_sync"] = {k: cb.isChecked() for k, cb in self.cat_checks.items()}
        return cfg, self.remote.text().strip()


# ─────────────────────────── 托盘弹出面板 ───────────────────────────

STATUS_TEXT = {
    "idle":      ("#888780", "就绪"),
    "syncing":   ("#378ADD", "正在检查远程…"),
    "uploading": ("#378ADD", "正在上传…"),
    "synced":    ("#0F6E56", "已同步"),
    "error":     ("#E24B4A", "同步失败"),
}

CATEGORY_ORDER = ["sessions", "identity", "skills", "config"]
KIND_STYLE = {
    "pull":  ("#0F6E56", "已拉取"),
    "push":  ("#378ADD", "已上传"),
    "error": ("#E24B4A", "失败"),
    "info":  ("#888780", "提示"),
    "check": ("#BA7517", "检查中"),
}


def _fmt_ago(ts):
    if not ts:
        return ""
    d = int(time.time() - float(ts))
    if d < 0:
        return "刚刚"
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{d // 60} 分钟前"
    if d < 86400:
        return f"{d // 3600} 小时前"
    return f"{d // 86400} 天前"


ICON_DIR = Path(__file__).resolve().parent / "icons"


def svg_pixmap(name, size=16, color=None):
    """
    从 icons/ 加载 SVG。
    color 给定时整体染色（CompositionMode_SourceIn），用来统一图标风格。
    """
    f = ICON_DIR / f"{name}.svg"
    if not f.is_file():
        return QPixmap()
    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return QPixmap()
    r = QSvgRenderer(str(f))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r.render(p)
    if color:
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(pm.rect(), QColor(color))
    p.end()
    return pm


def svg_icon(name, size=16, color=None):
    pm = svg_pixmap(name, size, color)
    return QIcon(pm) if not pm.isNull() else QIcon()


def broom_pixmap(size=14, color="#5F5E5A"):
    """画一个小扫把。emoji 在微软雅黑下不显示，只能自己画。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = p.pen()
    pen.setColor(QColor(color))
    pen.setWidth(1)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    k = size / 14.0
    # 刷头
    p.drawLine(int(2*k), int(11*k), int(7*k), int(6*k))
    p.drawLine(int(4*k), int(12.5*k), int(9*k), int(7.5*k))
    p.drawLine(int(2*k), int(11*k), int(4*k), int(12.5*k))
    p.drawLine(int(7*k), int(6*k), int(9*k), int(7.5*k))
    # 柄
    p.drawLine(int(8*k), int(5*k), int(12*k), int(1.5*k))
    p.end()
    return pm


def _avatar_pixmap(text, size=36, color=ACCENT):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, int(size * 0.26), int(size * 0.26))
    p.setPen(QColor("white"))
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(max(11, int(size * 0.44)))
    f.setWeight(QFont.Medium)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, (text or "?")[:1])
    p.end()
    return pm


class _CategoryBlock(QWidget):
    """一个可折叠的类别块：头部复选框控制整类，会话类条目还能单独勾。"""

    toggled = Signal()

    def __init__(self, key, label, items, item_checkable=False, parent=None):
        super().__init__(parent)
        self.key = key
        self._expanded = True
        self.item_checks = []

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.head = QWidget()
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.setStyleSheet("background:transparent;")
        h = QHBoxLayout(self.head)
        h.setContentsMargins(2, 5, 2, 5)
        h.setSpacing(6)

        self.arrow = QLabel("▾")
        self.arrow.setFixedWidth(11)
        self.arrow.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        h.addWidget(self.arrow)

        self.check = QCheckBox()
        self.check.setChecked(True)
        self.check.stateChanged.connect(lambda *_: self.toggled.emit())
        h.addWidget(self.check)

        nm = QLabel(label)
        nm.setStyleSheet(f"color:{TEXT}; font-size:13px;")
        h.addWidget(nm)

        cnt = QLabel(str(len(items)))
        cnt.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        h.addWidget(cnt)
        h.addStretch()
        v.addWidget(self.head)

        self.body = QWidget()
        self.body.setStyleSheet("background:transparent;")
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(40, 0, 2, 5)
        bl.setSpacing(2)
        for it in items:
            row = QWidget()
            row.setStyleSheet("background:transparent;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 1, 0, 1)
            rl.setSpacing(6)
            if item_checkable:
                cb = QCheckBox()
                cb.setChecked(True)
                cb.stateChanged.connect(lambda *_: self.toggled.emit())
                rl.addWidget(cb)
                self.item_checks.append((cb, it.get("key") or it.get("name")))
            lbl = QLabel()
            lbl.setStyleSheet(f"color:{MUTED}; font-size:12px;")
            fm = QFontMetrics(lbl.font())
            lbl.setText(fm.elidedText(it.get("name", ""), Qt.ElideMiddle, 200))
            rl.addWidget(lbl, 1)
            bl.addWidget(row)
        v.addWidget(self.body)

        self.head.mousePressEvent = lambda e: self.toggle()

    def toggle(self):
        self._expanded = not self._expanded
        self.body.setVisible(self._expanded)
        self.arrow.setText("▾" if self._expanded else "▸")

    def is_checked(self):
        return self.check.isChecked()

    def excluded_keys(self):
        return [k for cb, k in self.item_checks if not cb.isChecked()]


class TrayPanel(QWidget):
    """
    两个页签：待上传 / 待拉取。
    类别可折叠、可整类勾选；拉取页签下会话可逐条取消。
    """

    def __init__(self, provider, on_sync, on_settings, on_history, on_clear):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 固定尺寸：refresh 每秒都在跑，如果每次都改窗口高度，
        # 只要一次算错就会持续漂移（内容被裁、按钮消失）。
        self.setFixedSize(390, 480)
        self.setStyleSheet(QSS)
        self.provider = provider
        self._on_sync = on_sync
        self._on_settings = on_settings
        self._on_history = on_history
        self._on_clear = on_clear
        self.tab = "download"
        self.blocks = {}
        self._sig = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        self.card.setObjectName("panel")
        self.card.setStyleSheet(f"QFrame#panel{{background:{BG};border:1px solid {BORDER};"
                                f"border-radius:14px;}}")
        outer.addWidget(self.card)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(0)

        # ── 顶部：图标 + 标题 + 扫把 + 设置 ──
        head = QHBoxLayout()
        head.setSpacing(9)
        self.head_icon = QLabel()
        self.head_icon.setPixmap(tray_pixmap(30))
        self.head_icon.setFixedSize(30, 30)
        head.addWidget(self.head_icon)

        title = QLabel("SyncBuddy")
        title.setStyleSheet("font-size:15px; font-weight:500;")
        head.addWidget(title)
        head.addStretch()

        self.broom = QPushButton()
        brush = svg_icon("clean", 16, MUTED)
        if brush.isNull():
            brush = QIcon(broom_pixmap(15))     # SVG 缺失时退回手画
        self.broom.setIcon(brush)
        self.broom.setIconSize(QSize(16, 16))
        self.broom.setFixedSize(24, 24)
        self.broom.setToolTip("清空历史")
        self.broom.setCursor(Qt.PointingHandCursor)
        self.broom.setStyleSheet(
            "QPushButton{background:transparent;border:none;}"
            f"QPushButton:hover{{background:{BG_SOFT};border-radius:6px;}}")
        self.broom.clicked.connect(self._clear)
        head.addWidget(self.broom)

        gear = QPushButton()
        cog = svg_icon("setup", 16, MUTED)
        gear.setIcon(cog)
        gear.setIconSize(QSize(16, 16))
        gear.setFixedSize(24, 24)
        gear.setToolTip("设置")
        gear.setCursor(Qt.PointingHandCursor)
        gear.setStyleSheet(
            "QPushButton{background:transparent;border:none;}"
            f"QPushButton:hover{{background:{BG_SOFT};border-radius:6px;}}")
        gear.clicked.connect(self._settings)
        head.addWidget(gear)
        root.addLayout(head)

        # ── 状态行 ──
        st_row = QHBoxLayout()
        st_row.setContentsMargins(0, 7, 0, 0)
        st_row.setSpacing(7)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        st_row.addWidget(self.dot)
        self.status = QLabel("就绪")
        self.status.setStyleSheet(f"color:{TEXT}; font-size:13px;")
        st_row.addWidget(self.status)
        st_row.addStretch()
        self.when = QLabel("")
        self.when.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        st_row.addWidget(self.when)
        root.addLayout(st_row)

        root.addSpacing(10)
        root.addWidget(self._line())
        root.addSpacing(8)

        # ── 页签 ──
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_down = QPushButton("待拉取")
        self.tab_up = QPushButton("待上传")
        for b in (self.tab_down, self.tab_up):
            b.setCheckable(True)
            b.setFixedHeight(26)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:transparent;border:none;border-radius:6px;"
                f"color:{MUTED};font-size:12px;padding:0 10px;}}"
                f"QPushButton:checked{{background:{BG_SOFT};color:{TEXT};font-weight:500;}}")
            tabs.addWidget(b)
        self.tab_down.setChecked(True)
        self.tab_down.clicked.connect(lambda: self._switch("download"))
        self.tab_up.clicked.connect(lambda: self._switch("upload"))
        tabs.addStretch()
        root.addLayout(tabs)
        root.addSpacing(2)

        # ── 类别列表 ──
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                                  "QScrollBar:vertical{background:transparent;width:6px;}"
                                  "QScrollBar::handle:vertical{background:#D3D1C7;"
                                  "border-radius:3px;min-height:24px;}"
                                  "QScrollBar::add-line,QScrollBar::sub-line{height:0;}")
        self.host_w = QWidget()
        self.host_w.setStyleSheet("background:transparent;")
        self.host_lay = QVBoxLayout(self.host_w)
        self.host_lay.setContentsMargins(0, 2, 0, 2)
        self.host_lay.setSpacing(0)
        self.scroll.setWidget(self.host_w)
        root.addWidget(self.scroll, 1)      # stretch 1：占满中间剩余空间

        root.addSpacing(6)
        root.addWidget(self._line())
        root.addSpacing(8)

        # ── 底部 ──
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.btn_none = QPushButton("全部取消")
        self.btn_none.setObjectName("ghost")
        self.btn_none.setFixedHeight(32)
        self.btn_none.clicked.connect(self._clear_checks)
        self.btn_sync = QPushButton("同步所选")
        self.btn_sync.setFixedHeight(32)
        self.btn_sync.clicked.connect(self._do_sync)
        foot.addWidget(self.btn_none)
        foot.addWidget(self.btn_sync)
        root.addLayout(foot)

        self.refresh(full=True)

    def _line(self):
        ln = QFrame()
        ln.setFixedHeight(1)
        ln.setStyleSheet(f"background:{BORDER};border:none;")
        return ln

    # ── 页签切换 ──
    def _switch(self, which):
        self.tab = which
        self.tab_up.setChecked(which == "upload")
        self.tab_down.setChecked(which == "download")
        self.refresh(full=True)

    # ── 刷新 ──
    def refresh(self, full=True):
        data = self.provider() or {}
        status = data.get("status") or "idle"
        color, text = STATUS_TEXT.get(status, STATUS_TEXT["idle"])
        self.dot.setStyleSheet(f"background:{color};border-radius:4px;")
        self.status.setText(text)
        self.head_icon.setPixmap(tray_pixmap(30, status))

        last = data.get("last_push") or data.get("last_pull")
        self.when.setText(f"上次同步 {_fmt_ago(last)}" if last else "")

        plans = data.get("plans") or {}
        up = (plans.get("upload") or {}).get("total", 0)
        down = (plans.get("download") or {}).get("total", 0)
        self.tab_up.setText(f"待上传 {up}" if up else "待上传")
        self.tab_down.setText(f"待拉取 {down}" if down else "待拉取")

        sig = (self.tab, up, down, data.get("plan_ts"))
        if not full and sig == self._sig:
            return
        self._sig = sig

        # deleteLater 是延迟销毁，旧控件还挂在布局上会造成内容重叠；
        # setParent(None) 立即摘除，再交 deleteLater 回收内存
        while self.host_lay.count():
            it = self.host_lay.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.blocks = {}

        plan = plans.get(self.tab) or {}
        cats = plan.get("categories") or {}
        shown = 0
        for key in CATEGORY_ORDER:
            info = cats.get(key)
            if not info or not info.get("items"):
                continue
            blk = _CategoryBlock(key, info.get("label", key), info["items"],
                                 item_checkable=(self.tab == "download" and key == "sessions"))
            blk.toggled.connect(lambda *_: None)
            self.host_lay.addWidget(blk)
            self.blocks[key] = blk
            shown += 1

        if not shown:
            if not plans.get(self.tab):
                hint = "正在扫描…"
            elif self.tab == "upload":
                hint = "本机没有需要上传的改动"
            else:
                hint = "远程没有新内容"
            empty = QLabel(hint)
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color:{MUTED};font-size:12px;padding:60px 0;")
            self.host_lay.addWidget(empty)
        self.host_lay.addStretch()

        # 窗口尺寸固定，这里只让内部布局重排。
        # 千万别在这里 adjustSize：每秒调一次，一旦某次算出偏差就会累积，
        # 表现为内容被裁、按钮消失（清空历史后最容易触发）。
        self.host_w.adjustSize()
        self.host_w.updateGeometry()

    # ── 动作 ──
    def _clear_checks(self):
        for b in self.blocks.values():
            b.check.setChecked(False)
            for cb, _ in b.item_checks:
                cb.setChecked(False)

    def _do_sync(self):
        only = {k for k, b in self.blocks.items() if b.is_checked()}
        exclude = set()
        for b in self.blocks.values():
            exclude |= set(b.excluded_keys())
        self.hide()
        if self._on_sync:
            self._on_sync(self.tab, only, exclude)

    def _settings(self):
        self.hide()
        if self._on_settings:
            self._on_settings()

    def _clear(self):
        # 面板内容不依赖同步历史，清空后不需要重建列表；
        # 之前这里和 clear_history 各刷一次，连续重建两次最容易触发尺寸漂移
        if self._on_clear:
            self._on_clear()

    def _history(self):
        self.hide()
        if self._on_history:
            self._on_history()

    # ── 定位 ──
    def showEvent(self, ev):
        super().showEvent(ev)
        if getattr(self, "_tick", None) is None:
            self._tick = QTimer(self)
            self._tick.timeout.connect(lambda: self.refresh(full=False))
        self._tick.start(1000)

    def hideEvent(self, ev):
        if getattr(self, "_tick", None):
            self._tick.stop()
        super().hideEvent(ev)

    def show_near(self, tray_geo):
        self.refresh(full=True)
        scr = self.screen().availableGeometry() if self.screen() else None
        if scr and tray_geo:
            x = tray_geo.center().x() - self.width() // 2
            y = tray_geo.top() - self.height() - 8
            x = max(scr.left() + 8, min(x, scr.right() - self.width() - 8))
            if y < scr.top() + 8:
                y = tray_geo.bottom() + 8
            self.move(x, y)
        self.show()


class HistoryDialog(QDialog):
    """同步历史，从右键菜单打开。"""

    def __init__(self, events, clear_cb=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("同步历史")
        self.setMinimumSize(520, 420)
        self.setStyleSheet(QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel("同步历史")
        t.setObjectName("title")
        head.addWidget(t)
        head.addStretch()
        if clear_cb:
            btn = QPushButton("清空")
            btn.setObjectName("ghost")
            btn.clicked.connect(lambda: (clear_cb(), self.accept()))
            head.addWidget(btn)
        root.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 6, 0)
        v.setSpacing(2)

        if not events:
            empty = QLabel("还没有同步记录")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color:{MUTED};padding:80px 0;")
            v.addWidget(empty)
        else:
            for ev in events:
                v.addWidget(self._row(ev))
        v.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        root.addLayout(btns)

    def _row(self, ev):
        kind = ev.get("kind") or "info"
        color, verb = KIND_STYLE.get(kind, KIND_STYLE["info"])
        title = (ev.get("title") or "").strip()

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(4, 5, 4, 5)
        h.setSpacing(10)

        mark = title[:1] if title else {"pull": "↓", "push": "↑", "error": "!",
                                        "check": "…"}.get(kind, "·")
        av = QLabel()
        av.setPixmap(_avatar_pixmap(mark, 30, color))
        av.setFixedSize(30, 30)
        h.addWidget(av, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(1)
        t = QLabel()
        t.setStyleSheet(f"color:{TEXT};font-size:13px;")
        fm = QFontMetrics(t.font())
        t.setText(fm.elidedText(title or verb, Qt.ElideRight, 340))
        col.addWidget(t)
        sub = QLabel(ev.get("detail") or verb)
        sub.setStyleSheet(f"color:{MUTED};font-size:12px;")
        sub.setWordWrap(True)
        col.addWidget(sub)
        h.addLayout(col, 1)

        tm = QLabel(_fmt_ago(ev.get("ts")))
        tm.setStyleSheet("color:#B4B2A9;font-size:11px;")
        tm.setAlignment(Qt.AlignTop | Qt.AlignRight)
        h.addWidget(tm)
        return row
