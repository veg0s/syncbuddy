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
syncbuddy / core.py — 同步调度核心

两个入口：
    sync_in()   启动前：检查远程有没有新东西，拉下来，写进本机
    sync_out()  WorkBuddy 关闭后：导出本机状态，提交，上传

设计约束（都是踩过的坑）：
  1. 任何失败都不能阻塞 WorkBuddy 启动。出错就记日志，照常放行。
  2. WorkBuddy 运行时不能写 workbuddy.db，所以 import 只在启动前做。
  3. 上传失败要留 pending 标记，下次启动补传（应对崩溃 / 断网）。
  4. push 被拒（non-fast-forward）自动 fetch + 合并 + 重导 + 重试一次。
  5. 分叉合并不能丢数据：所有冲突取远程，再让合并式 export 把本机数据补回去。
"""

import json
import os
import _proc
import paths
import sys
import time
from datetime import datetime
from pathlib import Path

WB_SYNC = paths.WB_SYNC

# 必须先设环境变量：下面 load_wb_config() 会用 importlib 直接加载
# wbsync/import.py，该模块在 import 时就求值 ROOT = WB_SYNC_REPO 或自身目录。
# 没设的话它会在 wbsync/ 里找 config.json，然后报"缺少配置文件"。
os.environ.setdefault("WB_SYNC_REPO", str(WB_SYNC))

# 工具脚本和数据仓库分开放：脚本随启动器走，仓库只存数据。
# 新布局找不到时回退到仓库根，兼容老版本。
TOOLS = Path(__file__).resolve().parent / "wbsync"


def _script(name):
    p = TOOLS / name
    return p if p.is_file() else (WB_SYNC / name)


def _sync_env():
    """脚本靠 WB_SYNC_REPO 定位数据目录，不再依赖自己所在位置。"""
    e = os.environ.copy()
    e["WB_SYNC_REPO"] = str(WB_SYNC)
    return e


# 用 APP_DIR 而不是 __file__ 的目录：打包后 __file__ 在 _internal/ 里，
# 日志和状态写进去用户找不到，程序更新时还会被覆盖
STATE_FILE = paths.APP_DIR / "state.json"
LOG_DIR = paths.APP_DIR / "logs"


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    # pythonw 下 sys.stdout 可能是 None，print 会抛异常；不能让它带崩整个流程
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / f"{datetime.now():%Y-%m}.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _short_path(p: Path) -> str:
    """取 Windows 8.3 短路径。拿不到就返回原路径（可能含空格）。"""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, 512)
        if n:
            return buf.value.replace("\\", "/")
    except Exception:
        pass
    return p.as_posix()


def load_wb_config():
    """复用 wb-sync 的 config 加载逻辑（含本机路径映射）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("wbimp", _script("import.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_config(), mod.workbuddy_running


class State:
    """跨次运行的状态，主要用于崩溃后补传 + 托盘面板的历史展示。"""

    def __init__(self):
        self.data = {"last_pull": None, "last_push": None,
                     "pending": False, "last_error": None,
                     "status": "idle", "events": []}
        if STATE_FILE.is_file():
            try:
                self.data.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
            except Exception:
                pass

    def save(self):
        try:
            STATE_FILE.write_text(json.dumps(self.data, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        except Exception:
            pass

    def record(self, kind, title="", detail="", count=1):
        """
        记一条同步事件，供托盘面板展示。
        kind: check / pull / push / error / info
        """
        ev = {"ts": int(time.time()), "kind": kind,
              "title": (title or "")[:80], "detail": detail or "",
              "count": count}
        evs = list(self.data.get("events") or [])
        evs.insert(0, ev)
        self.data["events"] = evs[:30]
        self.save()

    def __getitem__(self, k):
        return self.data.get(k)

    def __setitem__(self, k, v):
        self.data[k] = v
        # 状态一变就记时间戳，供 UI 判断是否卡死
        if k == "status":
            self.data["status_ts"] = int(time.time())


class SyncBuddy:
    def __init__(self):
        self.cfg, self._wb_running_fn = load_wb_config()
        self.repo = WB_SYNC
        self.python = self.cfg["_local"].get("python") or sys.executable
        self.state = State()

    # ─────────────────────── git 基础 ───────────────────────

    def _remote_is_ssh(self):
        r = _proc.run(["git", "remote", "get-url", "origin"], cwd=self.repo,
                           capture_output=True, text=True, timeout=20)
        url = (r.stdout or "").strip()
        return url.startswith("git@") or url.startswith("ssh://")

    def _proxy(self):
        """
        返回 (git 额外参数, 环境变量)。

        HTTPS 远程：git 自己的 http.proxy 就够。
        SSH 远程：http.proxy 对它无效，必须用 ProxyCommand 建隧道。
                 Git for Windows 自带 connect.exe，正好拿来做 HTTP CONNECT。

        坑：ProxyCommand 里的路径不能含空格。即使加引号，git 传给 sh 时
        也会被拆断（实测报 "C:/Program: No such file or directory"）。
        必须转成 8.3 短路径，如 C:/PROGRA~1/Git/mingw64/bin/connect.exe。
        """
        p = self.cfg.get("proxy") or {}
        if not p.get("enabled"):
            return [], None

        url = (p.get("url") or "http://127.0.0.1:7890").rstrip("/")
        hostport = url.split("://", 1)[-1]
        args = ["-c", f"http.proxy={url}", "-c", f"https.proxy={url}"]

        env = os.environ.copy()
        connect = Path(r"C:\Program Files\Git\mingw64\bin\connect.exe")
        if self._remote_is_ssh():
            if connect.is_file():
                cpath = _short_path(connect)
                if " " in cpath:
                    log("⚠ connect.exe 拿不到 8.3 短路径，SSH 无法走代理（仍直连）")
                else:
                    env["GIT_SSH_COMMAND"] = (
                        f'ssh -o ProxyCommand="{cpath} -H {hostport} %h %p"'
                    )
            else:
                log("⚠ 配置了代理但找不到 connect.exe，SSH 仍走直连")
        return args, env

    def _git(self, *args, timeout=120):
        extra, env = self._proxy()
        return _proc.run(["git", *extra, *args], cwd=self.repo,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, env=env)

    def remote_url(self):
        r = _proc.run(["git", "remote", "get-url", "origin"], cwd=self.repo,
                      capture_output=True, text=True, timeout=20)
        return (r.stdout or "").strip()

    def remote_host(self):
        """远程仓库的主机名。连通性测试跟着它走，不写死 GitHub。"""
        return paths.remote_host(self.remote_url())

    def test_proxy(self):
        """连通性自检：直连和代理各试一次，返回两者结果。

        测的是**远程仓库所在主机**，不是写死的 github.com ——
        用 Gitee / GitLab / 自建 Gitea 时，测 GitHub 通不通没有参考价值。
        """
        import urllib.request
        p = self.cfg.get("proxy") or {}
        url = (p.get("url") or "http://127.0.0.1:7890").rstrip("/")

        host = self.remote_host()
        if not host:
            return {"direct": (False, "还未配置远程地址", 0),
                    "proxy": (False, "还未配置远程地址", 0),
                    "proxy_url": url, "host": ""}
        target = f"https://{host}"

        def _probe(proxy_url=None):
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}
                                            if proxy_url else {}))
            t0 = time.time()
            try:
                with opener.open(target, timeout=8) as r:
                    return True, r.status, int((time.time() - t0) * 1000)
            except Exception as ex:
                return False, str(ex)[:80], int((time.time() - t0) * 1000)

        return {"direct": _probe(None), "proxy": _probe(url),
                "proxy_url": url, "host": host}

    def fetch(self):
        r = self._git("fetch", "origin", "main", timeout=180)
        if r.returncode == 0:
            return
        msg = (r.stderr or r.stdout).strip()
        # 刚建的远程仓库一个 commit 都没有，没有 main 分支可 fetch。
        # 这不是故障，首次推送会把远端建起来。
        if "couldn't find remote ref" in msg or "no such remote" in msg.lower():
            log("  · 远程还没有 main 分支（首次推送会建）")
            return
        raise RuntimeError(f"fetch 失败：{msg[:200]}")

    def git_state(self):
        """
        返回 {state, ahead, behind}
        state ∈ same / remote_ahead / local_ahead / diverged / no_upstream
        """
        local = self._git("rev-parse", "HEAD")
        if local.returncode != 0:
            return {"state": "no_upstream", "ahead": 0, "behind": 0}
        remote = self._git("rev-parse", "origin/main")
        if remote.returncode != 0:
            return {"state": "no_upstream", "ahead": 0, "behind": 0}

        a = self._git("rev-list", "--count", "origin/main..HEAD").stdout.strip() or "0"
        b = self._git("rev-list", "--count", "HEAD..origin/main").stdout.strip() or "0"
        ahead, behind = int(a), int(b)

        dirty = bool(self._git("status", "--porcelain").stdout.strip())

        if local.stdout.strip() == remote.stdout.strip() and not dirty:
            return {"state": "same", "ahead": 0, "behind": 0}
        if ahead and behind:
            return {"state": "diverged", "ahead": ahead, "behind": behind}
        if behind:
            return {"state": "remote_ahead", "ahead": ahead, "behind": behind}
        if ahead or dirty:
            return {"state": "local_ahead", "ahead": ahead, "behind": behind}
        return {"state": "same", "ahead": 0, "behind": 0}

    # ─────────────────────── 导出 / 导入 ───────────────────────

    def _runner(self, name):
        """
        执行 wbsync 脚本的命令前缀。

        源码运行时用配置里的 python；打包成 exe 之后 sys.executable 就是
        exe 自己，得走 `--run` 分发 —— 那时磁盘上根本没有 python.exe。
        """
        script = _script(name)
        if getattr(sys, "frozen", False):
            return [sys.executable, "--run", str(script)]
        return [self.python, str(script)]

    def export(self, layer="all", only=None):
        cmd = self._runner("export.py") + ["--layer", layer]
        if only:
            cmd += ["--only", ",".join(sorted(only))]
        r = _proc.run(cmd, cwd=str(self.repo), capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=600,
                      env=_sync_env())
        if r.returncode != 0:
            raise RuntimeError(f"导出失败：{(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout

    def plan(self):
        """待上传清单（本机相对 data/ 变了什么），给面板的「待上传」用。"""
        r = _proc.run(self._runner("export.py") + ["--plan"],
                      cwd=str(self.repo), capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=300,
                      env=_sync_env())
        if r.returncode != 0:
            raise RuntimeError(f"预扫描失败：{(r.stderr or r.stdout).strip()[:200]}")
        return json.loads(r.stdout)

    def do_import(self, layer="all", exclude_ids=None, only=None):
        cmd = self._runner("import.py") + ["--apply", "--layer", layer]
        if exclude_ids:
            cmd += ["--exclude", ",".join(sorted(exclude_ids))]
        if only:
            cmd += ["--only", ",".join(sorted(only))]
        r = _proc.run(cmd, cwd=str(self.repo), capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=600,
                      env=_sync_env())
        if r.returncode != 0:
            raise RuntimeError(f"导入失败：{(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout

    def has_changes(self):
        return bool(self._git("status", "--porcelain").stdout.strip())

    def commit(self):
        if not self.has_changes():
            return False
        self._git("add", "-A")
        msg = f"sync: {self.cfg['_host']} {datetime.now():%Y-%m-%d %H:%M}"
        r = self._git("commit", "-m", msg, timeout=300)
        if r.returncode != 0:
            # 可能是只有 .plain-hashes 之类的时间变化，忽略
            log(f"  · 提交未产生变更：{(r.stdout or r.stderr).strip()[:120]}")
            return False
        return True

    def push(self):
        # -u 是幂等的：第一次顺便设好 upstream，之后正常推。
        # 只写裸 `push` 的话，新建仓库会报 "no upstream branch" 推不上去。
        r = self._git("push", "-u", "origin", "main", timeout=300)
        return r.returncode == 0, (r.stderr or r.stdout).strip()

    def merge_remote(self):
        """
        分叉合并：冲突一律取远程，随后由合并式 export 把本机数据补回来。
        这样远程独有的会话不会丢，本机较新的内容也会被重导覆盖。
        """
        r = self._git("merge", "origin/main", "--no-commit", "--no-ff", "-X", "theirs",
                      timeout=300)
        if r.returncode != 0:
            self._git("merge", "--abort")
            raise RuntimeError(f"合并失败：{(r.stderr or r.stdout).strip()[:200]}")
        return True

    # ─────────────────────── 差异预览 ───────────────────────

    def incoming(self):
        """远程有新会话吗？返回预览列表。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "wbprev", Path(__file__).resolve().parent / "preview.py")
        prev = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prev)
        return prev.build_preview(self.cfg, use_remote=True)

    # ─────────────────────── Part 1：启动前 ───────────────────────

    def _settle(self, rep):
        """
        把本轮结果收敛成终态。
        内部有多个提前 return 的分支（仓库一致、fetch 失败、用户跳过…），
        以前只在「拉取并导入成功」时才设 synced，导致最常见的情况
        ——仓库本来就一致——永远卡在「正在检查远程」。
        """
        if rep.get("error"):
            self.state["status"] = "error"
            self.state["last_error"] = str(rep["error"])
        else:
            self.state["status"] = "synced"
            self.state["last_error"] = None
        self.state.save()

    def sync_in(self, auto_pull=True, preview_hook=None, only=None, progress_cb=None):
        """外层包装：保证不管走哪条分支，状态都会落到终态。
        progress_cb(key, status, text) 用于启动窗的分步进度。"""
        self.state["status"] = "syncing"
        self.state.save()
        try:
            rep = self._sync_in_impl(auto_pull, preview_hook, only, progress_cb)
        except Exception as ex:
            log(f"⚠ 同步异常：{ex}")
            rep = {"error": str(ex), "pulled": False, "pushed": False,
                   "new_sessions": [], "imported": False, "skipped": None}
        self._settle(rep)
        return rep

    def _sync_in_impl(self, auto_pull=True, preview_hook=None, only=None, progress_cb=None):
        """
        返回报告 dict：
            pulled / pushed / new_sessions / imported / error / skipped
        """
        rep = {"pulled": False, "pushed": False, "new_sessions": [],
               "imported": False, "error": None, "skipped": None}
        picked = None  # None = 全要；list = 只要这些 id

        def step(key, status, text=None):
            if progress_cb:
                try:
                    progress_cb(key, status, text)
                except Exception:
                    pass

        step("check", "active")

        # 崩溃补传：上次没传完的，先补上
        if self.state["pending"]:
            log("发现上次未完成的上传，先补传…")
            try:
                self.export(only=only)
                if self.commit():
                    ok, _ = self.push()
                    if ok:
                        self.state["pending"] = False
                        self.state["last_push"] = time.time()
                        self.state.save()
                        rep["pushed"] = True
                        log("  补传完成")
            except Exception as ex:
                log(f"  补传失败（不影响启动）：{ex}")

        try:
            self.fetch()
            step("check", "done")
        except Exception as ex:
            rep["error"] = str(ex)
            step("check", "error")
            log(f"⚠ 无法连接远程，跳过拉取：{ex}")
            return rep

        st = self.git_state()
        log(f"仓库状态：{st['state']}（领先 {st['ahead']}，落后 {st['behind']}）")

        # 本机有未上传的改动：先推上去，避免 pull 覆盖
        if st["state"] in ("local_ahead", "diverged") and st["ahead"] >= 0:
            try:
                self.export(only=only)
                if self.commit():
                    ok, err = self.push()
                    if ok:
                        rep["pushed"] = True
                        self.state["last_push"] = time.time()
                        self.state.save()
                        log("  本机改动已上传")
                        st = self.git_state()
                    elif "rejected" in err or "non-fast-forward" in err:
                        log("  推送被拒，先合并远程再重试")
                        self.merge_remote()
                        self.export()
                        self.commit()
                        ok2, err2 = self.push()
                        if ok2:
                            rep["pushed"] = True
                            self.state["last_push"] = time.time()
                            self.state.save()
                            st = self.git_state()
                        else:
                            log(f"  重试仍失败：{err2[:120]}")
                            self.state["pending"] = True
                            self.state.save()
            except Exception as ex:
                log(f"  上传失败（继续尝试拉取）：{ex}")

        # 拉取
        if st["state"] in ("remote_ahead", "diverged") or st["behind"] > 0:
            try:
                items, stat = self.incoming()
                rep["new_sessions"] = items
                if items:
                    log(f"远程有 {len(items)} 个本机没有的会话")
                    if preview_hook:
                        decision = preview_hook(items, stat)
                        # hook 可以返回 bool（全要/全不要）或 (bool, picked_ids)
                        if isinstance(decision, tuple):
                            ok, picked = decision[0], decision[1]
                        else:
                            ok = bool(decision)
                        if not ok:
                            rep["skipped"] = "用户取消"
                            return rep
                if not auto_pull:
                    rep["skipped"] = "未启用自动拉取"
                    return rep

                # 远端一个 commit 都没有，没得拉。直接跳过，
                # 否则 merge origin/main 会报一串看不懂的错
                if st["state"] == "no_upstream":
                    step("pull", "skip", "远程还没有数据")
                    rep["skipped"] = "远程为空"
                    return rep

                step("pull", "active", f"拉取 {len(items) if items else 0} 个会话")
                if st["state"] == "diverged":
                    self.merge_remote()
                else:
                    r = self._git("merge", "--ff-only", "origin/main", timeout=300)
                    if r.returncode != 0:
                        self.merge_remote()
                self._git("add", "-A")
                r = self._git("commit", "-m",
                              f"merge: 拉取远程 {datetime.now():%Y-%m-%d %H:%M}", timeout=300)
                rep["pulled"] = True
                self.state["last_pull"] = time.time()
                self.state.save()
                log("  已合并远程数据")
                step("pull", "done")
            except Exception as ex:
                rep["error"] = str(ex)
                log(f"⚠ 拉取失败（不影响启动）：{ex}")
                return rep

        # 写进本机（WorkBuddy 此时必须未运行）
        if rep["pulled"]:
            if self._wb_running_fn():
                rep["skipped"] = "WorkBuddy 已在运行，未写入本地库"
                log("⚠ WorkBuddy 正在运行，跳过写库；重开 SyncBuddy 才会生效")
            else:
                try:
                    skip_ids = set()
                    if picked is not None:
                        all_ids = {it["id"] for it in (rep["new_sessions"] or [])}
                        skip_ids = all_ids - set(picked)
                        if skip_ids:
                            log(f"  按你的选择跳过 {len(skip_ids)} 个会话")
                    self.do_import(exclude_ids=skip_ids, only=only)
                    rep["imported"] = True
                    log("  已写入本机数据库")
                    # 逐条记入面板历史，让用户看得见拉了什么
                    step("write", "done")
                    for it in (rep["new_sessions"] or []):
                        if it["id"] not in skip_ids:
                            self.state.record("pull", it.get("title") or "（无标题）",
                                              "已拉取到本机")
                except Exception as ex:
                    rep["error"] = str(ex)
                    step("write", "error")
                    log(f"⚠ 写入本机失败：{ex}")

        return rep

    # ─────────────────────── Part 2：关闭后 ───────────────────────

    def _mark_error(self, msg):
        """统一记录失败：标记待补传 + 面板上显示红点。"""
        self.state["pending"] = True
        self.state["last_error"] = msg
        self.state["status"] = "error"
        self.state.record("error", "", str(msg)[:80])
        self.state.save()

    def sync_out(self, only=None):
        """外层包装：同样保证状态收敛，避免卡在「正在上传」。
        only 为 None 表示全部类别；否则只导出指定的类别。"""
        self.state["status"] = "uploading"
        self.state.save()
        try:
            rep = self._sync_out_impl(only)
        except Exception as ex:
            log(f"⚠ 上传异常：{ex}")
            rep = {"exported": False, "committed": False,
                   "pushed": False, "error": str(ex)}
        self._settle(rep)
        return rep

    def _sync_out_impl(self, only=None):
        rep = {"exported": False, "committed": False, "pushed": False, "error": None}
        changed = 0
        try:
            self.export(only=only)
            rep["exported"] = True
        except Exception as ex:
            rep["error"] = str(ex)
            log(f"⚠ 导出失败：{ex}")
            self.state["pending"] = True
            self.state["last_error"] = str(ex)
            self.state.save()
            return rep

        try:
            changed = len([l for l in self._git("status", "--porcelain").stdout.splitlines()
                           if l.strip()])
            rep["committed"] = self.commit()
        except Exception as ex:
            rep["error"] = str(ex)
            log(f"⚠ 提交失败：{ex}")
            return rep

        try:
            ok, err = self.push()
            if ok:
                rep["pushed"] = True
                self.state["pending"] = False
                self.state["last_push"] = time.time()
                self.state["last_error"] = None
                self.state["status"] = "synced"
                self.state.record("push", "",
                                  f"已上传 {changed} 项改动" if changed else "已上传",
                                  count=max(changed, 1))
                self.state.save()
                log("  已上传")
            else:
                if "rejected" in err or "non-fast-forward" in err:
                    log("  远程有新数据，合并后重试")
                    self.fetch()
                    self.merge_remote()
                    self.export()
                    self.commit()
                    ok2, err2 = self.push()
                    if ok2:
                        rep["pushed"] = True
                        self.state["pending"] = False
                        self.state["last_push"] = time.time()
                        self.state["status"] = "synced"
                        self.state.record("push", "", "合并另一台机器数据后上传成功")
                        self.state.save()
                        log("  合并后上传成功")
                    else:
                        rep["error"] = err2
                        self._mark_error(err2)
                        log(f"⚠ 上传失败，已标记待补传：{err2[:120]}")
                else:
                    rep["error"] = err
                    self._mark_error(err)
                    log(f"⚠ 上传失败，已标记待补传：{err[:120]}")
        except Exception as ex:
            rep["error"] = str(ex)
            self._mark_error(str(ex))
            log(f"⚠ 上传异常：{ex}")

        return rep


if __name__ == "__main__":
    sb = SyncBuddy()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        sb.fetch()
        print(json.dumps(sb.git_state(), ensure_ascii=False))
        print("pending:", sb.state["pending"])
    elif cmd == "in":
        print(json.dumps(sb.sync_in(), ensure_ascii=False, default=str)[:800])
    elif cmd == "out":
        print(json.dumps(sb.sync_out(), ensure_ascii=False, default=str))
    elif cmd == "preview":
        items, stat = sb.incoming()
        import preview as _p
        _p.fmt_preview(items, stat)
    elif cmd == "proxy-test":
        r = sb.test_proxy()
        host = r.get("host") or "（远程地址为空）"
        ok, info, ms = r["direct"]
        print(f"直连 {host}  : {'成功' if ok else '失败'}  {info}  {ms}ms")
        ok2, info2, ms2 = r["proxy"]
        print(f"经代理 {r['proxy_url']}  : {'成功' if ok2 else '失败'}  {info2}  {ms2}ms")
        enabled = (sb.cfg.get("proxy") or {}).get("enabled")
        print(f"当前配置：{'已启用代理' if enabled else '未启用代理（直连）'}")
        print(f"远程类型：{'SSH（走 connect.exe 隧道）' if sb._remote_is_ssh() else 'HTTPS（走 http.proxy）'}")
