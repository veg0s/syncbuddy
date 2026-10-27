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
syncbuddy / wizard.py — 首次运行配置向导

目标：换一台新机器时，除了「私钥」和「git 远程地址」，其余全部自动探测，
用户只需要按回车确认。

能自动拿到的：
    主机名        socket.gethostname()
    WorkBuddy    常见安装路径 / 运行中进程
    数据目录     %USERPROFILE%\\.workbuddy
    用户 UID     从 user-<uid>-personal 目录名反推
    工作区根     统计 sessions.cwd 的父目录，取占比最高的
    age 私钥     ~/.config/age/workwork.key
    Python       当前解释器

用法：
    python wizard.py           交互式配置
    python wizard.py --yes     全部采用探测结果（用于脚本/自动化）
    python wizard.py --show    只打印探测结果，不写文件
"""

import json
import os
import paths
import socket
import sqlite3
import _proc
import sys
from collections import Counter
from pathlib import Path

WB_SYNC = paths.WB_SYNC
CONFIG_FILE = WB_SYNC / "config.json"

EXE_CANDIDATES = [
    r"{localappdata}\Programs\WorkBuddy\WorkBuddy.exe",
    r"{programfiles}\WorkBuddy\WorkBuddy.exe",
    r"{localappdata}\WorkBuddy\WorkBuddy.exe",
]

AGE_CANDIDATES = [
    r"C:\Users\{user}\.config\age\workwork.key",
]

AGE_BIN_CANDIDATES = [
    r"{localappdata}\Microsoft\WinGet\Packages\FiloSottile.age_Microsoft.Winget.Source_8wekyb3d8bbwe\age\age.exe",
    r"C:\Program Files\age\age.exe",
]


def ask(prompt, default="", yes=False):
    if yes:
        return default
    try:
        v = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return v or default


def detect_exe():
    """先找常见安装位置，再问运行中的进程要路径。"""
    env = {
        "localappdata": os.environ.get("LOCALAPPDATA", ""),
        "programfiles": os.environ.get("ProgramFiles", r"C:\Program Files"),
    }
    for tpl in EXE_CANDIDATES:
        p = Path(tpl.format(**env))
        if p.is_file():
            return str(p)

    # 从运行中的进程取路径（PowerShell 稍慢，只在必要时用）
    try:
        r = _proc.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process WorkBuddy -ErrorAction SilentlyContinue | "
             "Select-Object -First 1 -ExpandProperty Path)"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace")
        p = (r.stdout or "").strip().splitlines()
        if p and Path(p[0]).is_file():
            return p[0]
    except Exception:
        pass
    return ""


def detect_uid(home: Path):
    """从 user-<uid>-personal 目录名反推 UID。"""
    if not home.is_dir():
        return "", None, None
    cands = [d for d in home.iterdir()
             if d.is_dir() and d.name.startswith("user-") and d.name.endswith("-personal")]
    if not cands:
        return "", None, None
    # 有多个时取最近修改的
    cands.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    personal = cands[0]
    uid = personal.name[len("user-"):-len("-personal")]
    uid_dir = home / uid
    storage = home / "storage" / personal.name
    return uid, personal, (uid_dir, storage)


def detect_workspace_root(home: Path):
    """统计 sessions.cwd 的父目录，取出现最多的那个。"""
    db = home / "workbuddy.db"
    if not db.is_file():
        return ""
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
        cwds = [r[0] for r in conn.execute("SELECT cwd FROM sessions WHERE cwd IS NOT NULL")]
        conn.close()
    except Exception:
        return ""
    if not cwds:
        return ""
    parents = Counter()
    for c in cwds:
        norm = c.replace("\\", "/").rstrip("/")
        parents[norm.rsplit("/", 1)[0]] += 1
    root = parents.most_common(1)[0][0]
    return root


def detect_all():
    home = Path(os.environ.get("USERPROFILE", "")) / ".workbuddy"
    uid, personal, extra = detect_uid(home)
    uid_dir = storage = None
    if extra:
        uid_dir, storage = extra

    # 私钥路径不猜：涉及隐私，一律由用户自己指定绝对路径
    age_key = ""

    env = {"localappdata": os.environ.get("LOCALAPPDATA", "")}
    age_bin = ""
    for tpl in AGE_BIN_CANDIDATES:
        p = Path(tpl.format(**env))
        if p.is_file():
            age_bin = str(p)
            break
    if not age_bin:
        age_bin = "age"  # 退回到 PATH

    return {
        "hostname": socket.gethostname(),
        "exe": detect_exe(),
        "workbuddy_home": str(home),
        "personal_dir": str(personal) if personal else "",
        "uid_dir": str(uid_dir) if uid_dir else "",
        "storage_dir": str(storage) if storage else "",
        "workspace_root": detect_workspace_root(home),
        "age_key_file": age_key,
        "age_binary": age_bin,
        "python": sys.executable,
        "uid": uid,
    }


def show(d):
    print("\n── 自动探测结果 ──")
    rows = [
        ("主机名", d["hostname"]),
        ("WorkBuddy", d["exe"] or "（未找到，需手动指定）"),
        ("数据目录", d["workbuddy_home"]),
        ("用户 UID", d["uid"] or "（未找到）"),
        ("工作区根", d["workspace_root"] or "（未能推断）"),
        ("age 私钥", d["age_key_file"] or "（需自己指定）"),
        ("age 程序", d["age_binary"]),
        ("Python", d["python"]),
    ]
    for k, v in rows:
        print(f"  {k:10s} {v}")


def main():
    yes = "--yes" in sys.argv
    show_only = "--show" in sys.argv

    print("=" * 60)
    print("SyncBuddy 配置向导")
    print("=" * 60)

    if not WB_SYNC.is_dir():
        sys.exit(f"找不到同步仓库：{WB_SYNC}")

    d = detect_all()
    show(d)

    if show_only:
        return

    # 现有配置
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.is_file() else {}
    hosts = cfg.get("hosts", {})
    host = d["hostname"]
    already = host in hosts
    if already:
        print(f"\n本机 '{host}' 已有配置段，将更新它。")

    # 必须人工确认的项
    r = _proc.run(["git", "remote", "get-url", "origin"], cwd=WB_SYNC,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    remote = (r.stdout or "").strip()

    print("\n── 需要确认 ──")
    remote_in = ask("git 远程", remote, yes)
    age_key_in = ask("age 私钥（绝对路径）", d.get("age_key_file") or "", yes)
    git_name = ask("git 用户名", "", yes) if not yes else ""
    git_email = ask("git 邮箱", "", yes) if not yes else ""

    # 缺项检查
    problems = []
    if not d["exe"]:
        problems.append("WorkBuddy 可执行文件未找到，托盘启动会失败")
    if not age_key_in:
        problems.append("age 私钥没填，memory 无法解密")
    if not d["workspace_root"]:
        problems.append("工作区根未推断出来，跨机路径重写会失效")

    if problems and not yes:
        print("\n⚠ 以下项需要你处理：")
        for p in problems:
            print(f"  - {p}")
        if not ask("\n仍然继续写入配置？(y/N)", "n", yes).lower().startswith("y"):
            print("已取消")
            return

    # 写入
    hosts[host] = {
        "exe": d["exe"],
        "workspace_root": d["workspace_root"],
        "workbuddy_home": d["workbuddy_home"],
        "personal_dir": d["personal_dir"],
        "uid_dir": d["uid_dir"],
        "storage_dir": d["storage_dir"],
        "age_key_file": age_key_in,
        "age_binary": d["age_binary"],
        "python": d["python"],
    }
    cfg.setdefault("version", 1)
    cfg["hosts"] = hosts
    cfg.setdefault("exclude_background_automation", True)
    cfg.setdefault("sync_all_sessions", True)
    cfg.setdefault("proxy", {"enabled": False, "url": "http://127.0.0.1:7890"})

    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"\n✔ 已写入 {CONFIG_FILE}")

    # git 身份
    if yes or not git_name:
        cur_name = _proc.run(["git", "config", "--global", "user.name"],
                                  capture_output=True, text=True).stdout.strip()
        cur_email = _proc.run(["git", "config", "--global", "user.email"],
                                   capture_output=True, text=True).stdout.strip()
        git_name = git_name or cur_name
        git_email = git_email or cur_email

    if git_name and git_email:
        _proc.run(["git", "config", "--global", "user.name", git_name])
        _proc.run(["git", "config", "--global", "user.email", git_email])
        print(f"✔ git 身份：{git_name} <{git_email}>")

    if remote and remote != remote_in:
        _proc.run(["git", "remote", "set-url", "origin", remote_in], cwd=WB_SYNC)
        print(f"✔ git 远程：{remote_in}")

    print("\n下一步：")
    print("  python doctor.py        体检")
    print("  python import.py        演练导入（不写盘）")
    print("  python import.py --apply --layer l1    只恢复文件层")


if __name__ == "__main__":
    main()
