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
SyncBuddy — 启动器 + 同步壳

流程：
    启动 → 检查远程 → 有差异就让你挑 → 拉起 WorkBuddy → 盯着它
         → WorkBuddy 退出 → 导出上传 → 托盘提示 → 自己退出

刻意做成「包住 WorkBuddy 生命周期」的原因：它的数据库在运行时被锁，
只有启动前和退出后才有安全的写窗口。与其到处加钩子，不如把生命周期管起来。

用法：
    pythonw syncbuddy.py        正常启动（无控制台）
    python  syncbuddy.py        带控制台，方便看日志
    python  syncbuddy.py --no-wait    只做启动前同步，不监听退出
"""

import atexit
import json
import os
import _proc
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _read_version():
    try:
        return (HERE / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


__version__ = _read_version()

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QMessageBox

import core
import paths
import gui
import watch

WB_SYNC = core.WB_SYNC


class Worker(QThread):
    """把耗时的同步放到后台，避免界面假死。"""

    progress = Signal(str, str, str)     # (step_key, status, text)
    done = Signal(object)

    def __init__(self, fn, *args, _progress=False, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self._with_progress = _progress

    def _emit(self, key, status, text=None):
        self.progress.emit(key, str(status), text or "")

    def run(self):
        try:
            if self._with_progress:
                result = self.fn(*self.args, progress_cb=self._emit, **self.kwargs)
            else:
                result = self.fn(*self.args, **self.kwargs)
        except Exception as ex:
            result = {"error": str(ex)}
        self.done.emit(result)


class SyncBuddyApp:
    def __init__(self, app):
        self.app = app
        self.cfg = None
        self.sb = None
        self.worker = None
        self.watcher = watch.ProcessWatcher(interval=2.0, debounce=2)
        self.splash = None
        self.wb_proc = None
        self._plan_cache = {}
        self._plan_error = None
        self._plan_ts = 0
        self._plan_worker = None
        self._modal_open = False
        self.sb = None
        self.cfg = None

        self._init_core()
        self._init_tray()
        self._init_poll()

    # ────────────── 初始化 ──────────────

    def _init_core(self):
        try:
            self.sb = core.SyncBuddy()
            self.cfg = self.sb.cfg
            return
        except SystemExit:
            pass

        # 首次运行 / 本机没配置段 → 直接弹引导，别让用户去敲命令
        if not self.show_setup():
            QMessageBox.critical(None, "SyncBuddy", "配置没完成，程序无法启动。")
            raise SystemExit(2)

        # 向导跑完了不代表配置一定对，第二次失败也必须报出来，
        # 否则 pythonw 下会静默退出（没有 stderr，用户只看到"什么都没发生"）
        try:
            self.sb = core.SyncBuddy()
            self.cfg = self.sb.cfg
        except SystemExit as ex:
            QMessageBox.critical(
                None, "SyncBuddy",
                f"配置保存后仍然无法加载：\n\n{ex}\n\n"
                f"请检查 {core.WB_SYNC}\\config.json")
            raise SystemExit(2)

    def show_setup(self):
        """跑一遍配置向导，返回是否完成。"""
        try:
            from setup_wizard import SetupWizard
        except Exception as ex:
            QMessageBox.critical(None, "SyncBuddy", f"加载向导失败：{ex}")
            return False
        self._modal_open = True
        try:
            w = SetupWizard(WB_SYNC)
            w.exec()
            return bool(w.ok)
        finally:
            self._modal_open = False

    def _init_tray(self):
        st = (self.sb.state["status"] if self.sb else None) or "idle"
        self._last_status = st
        icon = QIcon(gui.tray_pixmap(64, st))
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("SyncBuddy")

        menu = QMenu()
        self.act_open = QAction("打开 WorkBuddy", self.app)
        self.act_open.triggered.connect(self.launch_wb_now)
        menu.addAction(self.act_open)

        act_sync = QAction("立即同步", self.app)
        act_sync.triggered.connect(self.manual_sync)
        menu.addAction(act_sync)

        act_hist = QAction("同步历史", self.app)
        act_hist.triggered.connect(self.show_history)
        menu.addAction(act_hist)

        menu.addSeparator()

        act_settings = QAction("设置…", self.app)
        act_settings.triggered.connect(self.open_settings)
        menu.addAction(act_settings)

        menu.addSeparator()
        act_quit = QAction("退出", self.app)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.show()

        # 左键点图标 → 待同步面板
        self.panel = gui.TrayPanel(
            provider=self.panel_data,
            on_sync=self.on_panel_sync,
            on_settings=self.open_settings,
            on_history=self.show_history,
            on_clear=self.clear_history,
        )
        self.tray.activated.connect(self.on_tray_activated)

    def on_tray_activated(self, reason):
        # 设置/历史等模态窗开着时，托盘左键一律忽略：
        # 否则面板会插到模态窗上面，关掉后状态错乱
        if getattr(self, "_modal_open", False):
            return
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.refresh_plan()                      # 后台重算待同步清单
            self.panel.show_near(self.tray.geometry())

    def _init_poll(self):
        """2 秒一次看 WorkBuddy 在不在，驱动状态机。"""
        self.timer = QTimer()
        self.timer.timeout.connect(self.on_tick)
        self.timer.start(2000)

    # ────────────── 状态机 ──────────────

    def start(self):
        n = watch.count_workbuddy()
        core.log(f"启动检查：WorkBuddy 进程数 {n}")
        if n > 0:
            self.notify("WorkBuddy 已在运行",
                        "本次只会在它退出后上传。想启动前同步，请先关掉它再用 SyncBuddy 启动。")
            self.state = "watching"
            return

        self.state = "syncing"
        self.splash = gui.SplashWindow()
        self.splash.show()
        self.run_sync_in(only=self.enabled_categories() or None)

    def run_sync_in(self, only=None, exclude=None):
        # 面板里已经选好了就直接用，不再弹预览
        if exclude:
            def hook(items, _stat):
                picked = [it["id"] for it in items if it["id"] not in exclude]
                return True, picked
        else:
            hook = self.on_preview_needed
        self.worker = Worker(self.sb.sync_in, _progress=True,
                             preview_hook=hook, only=only)
        self.worker.progress.connect(self.on_sync_progress)
        self.worker.done.connect(self.on_sync_in_done)
        self.worker.start()

    def on_sync_progress(self, key, status, text):
        """后台线程报上来的分步进度，刷新启动窗。"""
        if not self.splash:
            return
        try:
            if text:
                self.splash.set_text(key, text)
            self.splash.set_status(key, status)
        except Exception:
            pass

    def on_preview_needed(self, items, stat):
        """
        在后台线程里被调用。必须回到主线程弹窗，所以用 blocking 方式：
        先隐藏 splash，同步投递到主线程并等待结果。
        """
        import threading
        result = {"ok": False}
        ev = threading.Event()

        def show():
            if self.splash:
                self.splash.hide()
            dlg = gui.PreviewDialog(items, stat)
            ok = dlg.exec() == gui.QDialog.Accepted
            result["ok"] = ok
            result["skip"] = [] if ok else [it["id"] for it in items]
            result["picked"] = dlg.selected_ids() if ok else []
            ev.set()

        QTimer.singleShot(0, show)
        ev.wait(timeout=300)
        if result["ok"]:
            return True, result.get("picked")
        return False, None

    def on_sync_in_done(self, rep):
        if self.splash:
            # 没走到拉取/写库的分支，标成跳过而不是留个空心圈
            if not rep.get("pulled"):
                self.splash.set_status("pull", "skip")
                self.splash.set_status("write", "skip")
            self.splash.set_status("launch", "active")

        err = rep.get("error")
        if err:
            self.notify("同步没成功，但不影响使用", str(err)[:160])
        elif rep.get("imported"):
            n = len(rep.get("new_sessions") or [])
            if n:
                self.notify("已同步", f"拉取了 {n} 个新任务")
        elif rep.get("skipped"):
            self.notify("跳过同步", str(rep["skipped"]))

        self.launch_wb_now()

    # ────────────── 拉起与等待 ──────────────

    def launch_wb_now(self):
        exe = (self.cfg["_local"].get("exe") if self.cfg else "") or ""
        if not exe or not Path(exe).is_file():
            self.notify("找不到 WorkBuddy", "请用 wizard.py 重新配置 exe 路径")
            if self.splash:
                self.splash.set_status("launch", "error")
                self.splash.finish(1500)
                self.splash = None
            return
        try:
            self.wb_proc = _proc.popen([exe], close_fds=True)
        except Exception as ex:
            self.notify("启动失败", str(ex)[:160])
            return
        self.state = "watching"
        self.watcher = watch.ProcessWatcher(interval=2.0, debounce=2)

        if self.splash:
            self.splash.set_status("launch", "done")
            self.splash.finish()
            self.splash = None

    def on_tick(self):
        """轮询驱动：托盘图标跟随状态 + 卡死兜底 + 在 watching 时判断退出。"""
        st = (self.sb.state["status"] if self.sb else None) or "idle"

        # 兜底：同步线程万一卡住，不能永远显示「正在检查」
        if st in ("syncing", "uploading"):
            ts = self.sb.state["status_ts"] or 0
            if ts and time.time() - ts > 300:
                core.log(f"⚠ 状态 {st} 已超过 5 分钟，标记为超时")
                self.sb.state["status"] = "error"
                self.sb.state["last_error"] = "同步超时，请检查网络或代理"
                self.sb.state.record("error", "", "同步超时，请检查网络或代理")
                self.sb.state.save()
                st = "error"

        # 图标跟着状态走，不打开面板也能看出在干嘛（↔ 就绪 / … 同步中 / ↑ 上传 / ✓ 完成 / ! 失败）
        if st != getattr(self, "_last_status", None):
            self.tray.setIcon(QIcon(gui.tray_pixmap(64, st)))
            self._last_status = st

        if getattr(self, "state", "") != "watching":
            return
        ev = self.watcher.step()
        if ev == "exit":
            self.state = "uploading"
            self.notify("WorkBuddy 已关闭", "正在上传本次改动…")
            self.run_sync_out(only=self.enabled_categories() or None)

    def run_sync_out(self, only=None):
        self.worker = Worker(self.sb.sync_out, only=only)
        self.worker.done.connect(self.on_sync_out_done)
        self.worker.start()

    def on_sync_out_done(self, rep):
        if rep.get("pushed"):
            self.notify("已上传", "本次改动已同步到仓库")
        elif rep.get("error"):
            self.notify("上传未完成", "已标记待补传，下次启动会自动重试")
        else:
            self.notify("无需上传", "本次没有变化")

        # 包装器模式下，WorkBuddy 关了且传完了就该退出
        if "--no-wait" not in sys.argv:
            QTimer.singleShot(1500, self.quit)

    # ────────────── 待同步计划 ──────────────

    def enabled_categories(self):
        """设置里勾选的类别。自动同步只处理这些，关掉的一概不碰。"""
        auto = (self.cfg or {}).get("auto_sync") or {}
        return {k for k in ("identity", "skills", "tasks", "config", "sessions")
                if auto.get(k, True)}

    def panel_data(self):
        d = dict(self.sb.state.data) if self.sb else {}
        d["plans"] = self._plan_cache
        d["plan_ts"] = self._plan_ts
        d["auto_sync"] = (self.cfg or {}).get("auto_sync") or {}
        return d

    def refresh_plan(self):
        """后台算待同步清单，算完刷新面板（避免打开面板时卡住）。"""
        if self._plan_worker and self._plan_worker.isRunning():
            return
        self._plan_worker = Worker(self._compute_plan)
        self._plan_worker.done.connect(self._on_plan_done)
        self._plan_worker.start()

    def _compute_plan(self):
        up = self.sb.plan()
        # 远程预览失败（网络/仓库状态）不该让整个扫描失败 ——
        # 待上传清单是本机的，跟远程没关系
        try:
            items, _stat = self.sb.incoming()
        except Exception as ex:
            print(f"  远程预览失败：{ex}")
            items = []
        down = {
            "total": len(items),
            "categories": {
                "sessions": {
                    "label": "会话",
                    "items": [{"name": it.get("title") or "（无标题）", "key": it["id"]}
                              for it in items],
                }
            },
        }
        return {"upload": up, "download": down, "ts": int(time.time())}

    def _on_plan_done(self, res):
        if not isinstance(res, dict):
            return
        if res.get("error"):
            # 扫描失败必须让用户看见。之前这里直接跳过，缓存永远是空，
            # 界面就无限停在「正在扫描…」，看起来像卡死
            self._plan_error = str(res["error"])
            print(f"⚠ 预扫描失败：{self._plan_error}")
            self._plan_cache = {
                "upload": {"total": 0, "categories": {}, "error": self._plan_error},
                "download": {"total": 0, "categories": {}},
            }
        elif res.get("upload"):
            self._plan_error = None
            self._plan_cache = {"upload": res["upload"],
                                "download": res.get("download") or {}}
        self._plan_ts = res.get("ts") or int(time.time())
        try:
            if self.panel.isVisible():
                self.panel.refresh(full=True)
        except Exception:
            pass

    # ────────────── 面板动作 ──────────────

    def on_panel_sync(self, direction, only, exclude):
        if not only:
            self.notify("没有选中类别", "勾选要同步的类别后再点同步")
            return
        if direction == "upload":
            self.run_sync_out(only=only)
        else:
            self.run_sync_in(only=only, exclude=exclude)

    def show_history(self):
        events = list(self.sb.state["events"] or []) if self.sb else []
        self._modal_open = True
        try:
            gui.HistoryDialog(events, clear_cb=self.clear_history).exec()
        finally:
            self._modal_open = False

    def clear_history(self):
        if not self.sb:
            return
        self.sb.state["events"] = []
        self.sb.state.save()
        self.notify("已清空", "同步历史已清空")

    # ────────────── 其它动作 ──────────────

    def manual_sync(self):
        if watch.count_workbuddy() > 0:
            self.notify("WorkBuddy 正在运行", "请先关闭它再同步")
            return
        self.notify("开始同步", "正在导出并上传…")
        self.run_sync_out(only=self.enabled_categories() or None)

    def open_settings(self):
        self._modal_open = True
        dlg = gui.SettingsDialog(self.cfg, WB_SYNC)
        accepted = dlg.exec() == gui.QDialog.Accepted
        self._modal_open = False
        if accepted and getattr(dlg, "wants_setup", False):
            if self.show_setup():
                self._init_core()
            return
        if accepted:
            cfg, remote = dlg.result_config()
            # 写回 config.json（只改 proxy，不动 hosts）
            try:
                path = WB_SYNC / "config.json"
                cur = json.loads(path.read_text(encoding="utf-8"))
                cur["proxy"] = cfg["proxy"]
                path.write_text(json.dumps(cur, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                _proc.run(["git", "remote", "set-url", "origin", remote],
                               cwd=WB_SYNC, capture_output=True)
                # 重新加载
                self._init_core()
                self.notify("设置已保存", "代理与远程地址已更新")
            except Exception as ex:
                self.notify("保存失败", str(ex)[:160])

    def open_log(self):
        d = HERE / "logs"
        d.mkdir(exist_ok=True)
        _proc.popen(["explorer", str(d)])

    def notify(self, title, msg):
        try:
            self.tray.showMessage(title, msg, QSystemTrayIcon.Information, 4000)
        except Exception:
            pass

    def quit(self):
        try:
            self.timer.stop()
        except Exception:
            pass
        self.tray.hide()
        self.app.quit()


class _Tee:
    """同时写多个流。任何一个失败都不影响其它的。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def _setup_io():
    """
    日志必须落盘，不能靠"有没有控制台"来决定。

    windowed exe 的 sys.stdout 不是 None，是 GBK 编码的包装器。之前按
    `is None` 判断，结果打包版一个字的日志都没写 —— 出了问题完全没法查，
    用户只看到"卡住了"。

    现在无条件把文件流接进去：有控制台就两头都写，没有就只写文件。
    顺带统一成 UTF-8，避免 GBK 下中文和 ✔ 报 UnicodeEncodeError。
    """
    d = paths.APP_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    stream = open(d / "runtime.log", "a", encoding="utf-8", buffering=1)

    targets = []
    for name in ("stdout", "stderr"):
        cur = getattr(sys, name, None)
        if cur is not None:
            try:
                cur.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
            targets.append(cur)
    targets.append(stream)

    tee = _Tee(*targets)
    sys.stdout = tee
    sys.stderr = tee


def _install_crash_log():
    """兜底：未捕获异常落盘。"""
    import traceback

    def hook(exc_type, exc, tb):
        try:
            d = HERE / "logs"
            d.mkdir(exist_ok=True)
            with open(d / "crash.log", "a", encoding="utf-8") as f:
                f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                f.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


def _run_script(argv):
    """
    打包成 exe 之后没有独立的 python 解释器，同步引擎（wbsync/*.py）
    靠这个入口在子进程里跑：

        SyncBuddy.exe --run <脚本路径> [脚本自己的参数...]

    run_path 执行的是磁盘上的明文脚本，所以 wbsync/ 必须作为数据文件
    一起打包，运行时和 exe 同目录。
    """
    import runpy

    # windowed exe 的 sys.stdout 可能是 None，也可能是 GBK 编码的包装器。
    # 后者一写 ✔ 这类字符就 UnicodeEncodeError，所以统一压成 UTF-8。
    for _name in ("stdout", "stderr"):
        _stream = getattr(sys, _name, None)
        if _stream is None:
            setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
        else:
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    # 相对路径按 exe 所在目录解析。workflow 或别的脚本在仓库根调用时，
    # cwd 不是程序目录，直接交给 runpy 会找错地方。
    from pathlib import Path as _P
    _script_path = _P(argv[2])
    if not _script_path.is_absolute():
        _script_path = paths.APP_DIR / _script_path

    sys.argv = [str(_script_path)] + argv[3:]
    runpy.run_path(str(_script_path), run_name="__main__")


def main():
    # 子进程身份：转发给 wbsync 脚本，别去初始化托盘
    if len(sys.argv) >= 3 and sys.argv[1] == "--run":
        _run_script(sys.argv)
        return 0

    # 主程序才需要清进程树。--run 分支不能注册 —— taskkill 会把自己也干掉，
    # 退出码就变成非 0，CI 里的校验会误判成失败。
    atexit.register(_proc.kill_tree)

    _setup_io()
    _install_crash_log()
    print(f"\n=== SyncBuddy 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} ===")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("SyncBuddy")

    # 单实例：双击两次会出现两套 2 秒轮询，弹窗频率翻倍
    # 冲突时静默退出，弹窗反而会让用户以为卡住了
    from PySide6.QtCore import QLockFile
    lock = QLockFile(str(paths.APP_DIR / ".syncbuddy.lock"))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(200):
        core.log("已有实例在运行，本次启动退出")
        sys.exit(0)
    app._single_instance_lock = lock   # 必须留引用，否则锁会被回收

    sba = SyncBuddyApp(app)
    QTimer.singleShot(200, sba.start)
    rc = app.exec()
    print(f"=== SyncBuddy 退出 rc={rc} {time.strftime('%H:%M:%S')} ===")
    sys.exit(rc)


if __name__ == "__main__":
    main()
