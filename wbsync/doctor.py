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
wb-sync / doctor.py — 同步体系体检

查这几件事：环境齐不齐、数据全不全、索引对得上正文吗、路径能不能映射、
仓库有没有未提交的东西。

    python doctor.py            常规体检
    python doctor.py --verify   额外校验全部文件 sha256（慢，约 30 秒）
"""

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 仓库位置由启动器通过 WB_SYNC_REPO 传进来；没有就退回脚本目录（兼容旧布局）
ROOT = Path(os.environ.get("WB_SYNC_REPO") or HERE).resolve()
DATA = ROOT / "data"
CONFIG_FILE = ROOT / "config.json"

results = []


def check(level, title, detail=""):
    results.append((level, title, detail))
    icon = {"ok": "✔", "warn": "!", "fail": "✘"}[level]
    print(f"  [{icon}] {title}" + (f"  {detail}" if detail else ""))


def sha256_of(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="校验全部文件 sha256")
    args = ap.parse_args()

    host = socket.gethostname()
    print("=" * 62)
    print(f"wb-sync 体检   本机 {host}")
    print("=" * 62)

    # ── 配置 ──
    print("\n[配置]")
    cfg = None
    if not CONFIG_FILE.is_file():
        check("fail", "config.json 不存在")
        return summary()
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    check("ok", "config.json 可解析", f"v{cfg.get('version')}")

    local = cfg.get("hosts", {}).get(host)
    if not local:
        check("fail", f"缺少本机 '{host}' 的配置段",
              f"已有：{', '.join(cfg.get('hosts', {}))}")
        return summary()
    cfg["_host"] = host
    cfg["_local"] = local
    check("ok", "本机配置段存在")

    for key in ("workspace_root", "workbuddy_home"):
        p = Path(local.get(key, ""))
        if p.is_dir():
            check("ok", f"{key}", str(p))
        else:
            check("warn", f"{key} 目录不存在", str(p))

    # ── 工具链 ──
    print("\n[工具链]")
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=10)
        check("ok" if r.returncode == 0 else "fail", "git", r.stdout.strip())
    except Exception:
        check("fail", "git 不可用")

    age_bin = local.get("age_binary", "age")
    if Path(age_bin).is_file() or age_bin == "age":
        try:
            r = subprocess.run([age_bin, "--version"], capture_output=True, text=True, timeout=10)
            check("ok" if r.returncode == 0 else "warn", "age", r.stdout.strip() or "存在但不可执行")
        except Exception:
            check("warn", "age 无法执行", age_bin)
    else:
        check("warn", "age_binary 路径不对", age_bin)

    key = Path(local.get("age_key_file", ""))
    if key.is_file():
        check("ok", "age 私钥存在", key.name)
    else:
        check("fail", "age 私钥缺失，memory 无法解密", str(key))

    # ── 数据完整性 ──
    print("\n[数据]")
    manifest_file = DATA / "manifest.json"
    if not manifest_file.is_file():
        check("fail", "manifest.json 缺失，先跑 export.py")
        return summary()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    check("ok", "manifest 可用",
          f"{manifest['file_count']} 文件 / {manifest['total_bytes'] / 1e6:.1f} MB"
          f" / {manifest['exported_at'][:19]}")

    missing = [e["path"] for e in manifest["files"] if not (DATA / e["path"]).is_file()]
    if missing:
        check("fail", f"{len(missing)} 个文件在 data/ 里缺失", missing[0])
    else:
        check("ok", "manifest 所列文件齐全")

    if args.verify:
        bad = []
        for e in manifest["files"]:
            p = DATA / e["path"]
            if p.is_file() and sha256_of(p) != e["sha256"]:
                bad.append(e["path"])
        if bad:
            check("fail", f"{len(bad)} 个文件校验和不符", bad[0])
        else:
            check("ok", "全部文件 sha256 校验通过")

    # ── 会话一致性 ──
    print("\n[会话]")
    idx_file = DATA / "sessions" / "index.jsonl"
    if not idx_file.is_file():
        check("fail", "sessions/index.jsonl 缺失")
        return summary()
    idx = [json.loads(l) for l in idx_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    ids = {r["id"] for r in idx}
    files = {p.stem for p in (DATA / "sessions" / "transcripts").rglob("*.jsonl")}
    check("ok", "索引", f"{len(ids)} 条")
    check("ok", "正文", f"{len(files)} 个")
    if idx and all(r.get("_host") for r in idx):
        hosts = {r["_host"] for r in idx}
        check("ok", "来源主机标记完整", ", ".join(hosts))

    orphan = files - ids
    no_body = ids - files
    if orphan:
        check("warn", f"{len(orphan)} 个正文没有对应索引", sorted(orphan)[0][:12])
    if no_body:
        check("warn", f"{len(no_body)} 条索引没有正文（可能是源端已清理）")

    # ── 路径映射 ──
    print("\n[路径映射]")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("wbimp", ROOT / "import.py")
        wbimp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wbimp)
        same = remap = unmapped = 0
        bad_list = []
        for r in idx:
            new, state = wbimp.remap_path(r.get("cwd"), r.get("_host", ""), cfg)
            if state == "same":
                same += 1
            elif state == "remapped":
                remap += 1
            else:
                unmapped += 1
                bad_list.append(r.get("cwd", ""))
        check("ok", "原样可用", f"{same} 条")
        if remap:
            check("ok", "可重写", f"{remap} 条")
        if unmapped:
            check("warn", f"{unmapped} 条无法映射到本机", bad_list[0][:60])
        else:
            check("ok", "全部 cwd 可解析")
    except Exception as ex:
        check("warn", "路径映射检查跳过", str(ex)[:60])

    # ── 数据库 ──
    print("\n[数据库]")
    db = Path(local["workbuddy_home"]) / "workbuddy.db"
    if not db.is_file():
        check("fail", "workbuddy.db 不存在", str(db))
    else:
        try:
            conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
            cols = {d[1] for d in conn.execute("PRAGMA table_info(sessions)")}
            n = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
            conn.close()
            check("ok", "sessions 表可读", f"{n} 条")
            need = {"id", "cwd", "title", "status", "updated_at", "is_background_automation"}
            if need.issubset(cols):
                check("ok", "schema 字段齐全")
            else:
                check("fail", "schema 缺字段", str(need - cols))
        except Exception as ex:
            check("fail", "读库失败", str(ex)[:60])

    # ── 仓库 ──
    print("\n[仓库]")
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        dirty = [l for l in r.stdout.splitlines() if l.strip()]
        if dirty:
            check("warn", f"{len(dirty)} 个改动未提交", dirty[0][:50])
        else:
            check("ok", "工作区干净")

        r = subprocess.run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            ahead = r.stdout.strip()
            if ahead and ahead != "0":
                check("warn", f"{ahead} 个提交未推送")
            else:
                check("ok", "已与远程同步")
        else:
            check("warn", "没有上游分支", "首次推送用 git push -u origin main")
    except Exception as ex:
        check("warn", "git 检查跳过", str(ex)[:60])

    return summary()


def summary():
    print("\n" + "=" * 62)
    n_ok = sum(1 for l, _, _ in results if l == "ok")
    n_warn = sum(1 for l, _, _ in results if l == "warn")
    n_fail = sum(1 for l, _, _ in results if l == "fail")
    print(f"通过 {n_ok}   警告 {n_warn}   失败 {n_fail}")
    if n_fail:
        print("有失败项，先修再同步。")
    elif n_warn:
        print("可以同步，但留意上面的警告。")
    else:
        print("全部正常。")
    print("=" * 62)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
