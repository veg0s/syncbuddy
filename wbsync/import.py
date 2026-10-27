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
wb-sync / import.py — 把仓库里的状态导入本机

默认是 **演练模式**，只报告将要做什么，不写任何文件。
确认无误后加 --apply 才真正落地。

写 workbuddy.db 前会强制：
  1. 检查 WorkBuddy 是否在运行（在运行则中止，除非 --force）
  2. 备份整库为 workbuddy.db.bak.<时间戳>
  3. 校验 schema 版本，字段对不上就中止

用法：
    python import.py                   演练，看报告
    python import.py --apply           真导入（全部）
    python import.py --apply --layer l1  只导文件层，不碰数据库
    python import.py --apply --layer l2  只导会话层
"""

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 仓库位置由启动器通过 WB_SYNC_REPO 传进来；没有就退回脚本目录（兼容旧布局）
ROOT = Path(os.environ.get("WB_SYNC_REPO") or HERE).resolve()
DATA = ROOT / "data"
CONFIG_FILE = ROOT / "config.json"
BACKUP_ROOT = ROOT / "backup"

SKIP_NAMES = {"node_modules", "__pycache__", ".git", ".venv", "venv",
              ".DS_Store", "Thumbs.db", "desktop.ini"}


def log(msg=""):
    print(msg, flush=True)


def load_config():
    if not CONFIG_FILE.is_file():
        sys.exit(f"缺少配置文件：{CONFIG_FILE}")
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    host = socket.gethostname()
    hcfg = cfg.get("hosts", {}).get(host)
    if not hcfg:
        sys.exit(f"config.json 缺少本机 '{host}' 的配置段，请先补齐路径映射。")
    cfg["_host"] = host
    cfg["_local"] = hcfg
    return cfg


def workbuddy_running():
    """判断 WorkBuddy 主进程是否在跑。写库前必须为 False。"""
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WorkBuddy.exe"],
                           capture_output=True, timeout=15)
        # 中文系统的 tasklist 输出是 GBK，先按 utf-8 试再退回
        for enc in ("utf-8", "gbk", "cp936"):
            try:
                out = r.stdout.decode(enc)
                if "WorkBuddy" in out or "信息" in out or "INFO" in out.upper():
                    return "WorkBuddy.exe" in out
            except UnicodeDecodeError:
                continue
        return b"WorkBuddy.exe" in r.stdout
    except Exception:
        return False


def cwd_to_slug(cwd: str) -> str:
    """把 cwd 编码成 projects 目录名，如 C:\\Users\\x\\A → c-Users-x-A"""
    s = cwd.replace(":", "").replace("\\", "-").replace("/", "-")
    return s[0].lower() + s[1:] if s else s


def remap_path(p, src_host, cfg):
    """
    把源主机的绝对路径重写成本机路径。
    返回 (新路径, 状态)，状态 ∈ {same, remapped, unmapped}
    """
    if not p:
        return p, "same"
    src_root = cfg.get("hosts", {}).get(src_host, {}).get("workspace_root", "")
    dst_root = cfg["_local"]["workspace_root"]
    norm = p.replace("\\", "/")

    if src_root and norm.lower().startswith(src_root.replace("\\", "/").lower()):
        tail = norm[len(src_root.replace("\\", "/")):]
        new = dst_root.rstrip("/") + tail
        if new == norm:
            return new, "same"
        return new, "remapped"
    # 不在工作区根下的路径：本机若原样存在就直接用
    if Path(norm).exists():
        return norm, "same"
    return norm, "unmapped"


def backup_path(p: Path, tag: str, keep=False) -> Path:
    """把文件/目录备份到 backup/<tag>/ 下，返回备份路径。"""
    if not p.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = BACKUP_ROOT / f"{ts}-{tag}" / p.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if p.is_dir():
        shutil.copytree(p, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(p, dst)
    return dst


def merge_tree(src: Path, dst: Path, report, label: str, apply: bool):
    """
    目录合并：仓库优先覆盖同名文件，本机独有的保留。
    冲突（本机内容与仓库不同）记入 report。
    """
    if not src.is_dir():
        return
    added = updated = kept = conflict = 0
    for item in src.rglob("*"):
        if not item.is_file() or item.name in SKIP_NAMES:
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if not target.exists():
            added += 1
            if apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
        else:
            same = target.stat().st_size == item.stat().st_size
            if same:
                kept += 1
            else:
                updated += 1
                conflict += 1
                report.append(f"{label}: 覆盖本机改动 → {rel.as_posix()}")
                if apply:
                    shutil.copy2(item, target)
    log(f"  · {label}: 新增 {added}，相同 {kept}，覆盖 {updated}")


# ─────────────────────────── L1 文件层 ───────────────────────────

def import_identity(cfg, apply, report):
    src = DATA / "identity"
    if not src.is_dir():
        return
    local = cfg["_local"]
    targets = [
        Path(local.get("personal_dir") or local["workbuddy_home"]),
        Path(local["workbuddy_home"]),
    ]
    for t in targets:
        if not t.is_dir():
            continue
        for f in src.glob("*.md"):
            dst = t / f.name
            if dst.exists() and dst.read_bytes() == f.read_bytes():
                continue
            if apply:
                backup_path(dst, "identity")
                shutil.copy2(f, dst)
        log(f"  · 身份文件 → {t.name}")


def import_memory(cfg, apply, report):
    """解密 age 密文写回 memory/。私钥必须存在，否则跳过并告警。"""
    src = DATA / "memory"
    if not src.is_dir():
        return
    local = cfg["_local"]
    key = Path(local.get("age_key_file", ""))
    age_bin = local.get("age_binary", "age")

    if not key.is_file():
        log(f"  ! 找不到私钥 {key}，跳过记忆恢复")
        report.append("memory: 私钥缺失，未恢复（记忆无法解密）")
        return

    dst_dir = Path(local["workbuddy_home"]) / "memory"
    n = 0
    for f in sorted(src.rglob("*.age")):
        rel = f.relative_to(src)
        out = dst_dir / str(rel)[:-4]  # 去掉 .age
        n += 1
        if not apply:
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            backup_path(out, "memory")
        r = subprocess.run(
            [age_bin, "-d", "-i", str(key), "-o", str(out), str(f)],
            capture_output=True, text=True)
        if r.returncode != 0:
            log(f"  ! 解密失败 {rel}: {r.stderr.strip()[:120]}")
            report.append(f"memory: 解密失败 {rel}")
        elif out.exists():
            out.unlink() if False else None
    log(f"  · 记忆 {n} 个文件{'（演练）' if not apply else ' 已解密恢复'}")


def import_tree_dirs(cfg, apply, report, labels=("skills", "tasks", "plans")):
    local = cfg["_local"]
    home = Path(local["workbuddy_home"])
    for label in labels:
        src = DATA / label
        if src.is_dir():
            merge_tree(src, home / label, report, label, apply)


def import_config(cfg, apply, report):
    local = cfg["_local"]
    home = Path(local["workbuddy_home"])
    uid_dir = Path(local["uid_dir"]) if local.get("uid_dir") else None
    storage_dir = Path(local["storage_dir"]) if local.get("storage_dir") else None
    src = DATA / "config"
    if not src.is_dir():
        return

    # mcp.json 直接覆盖
    m = src / "mcp.json"
    if m.is_file():
        dst = home / "mcp.json"
        if apply:
            backup_path(dst, "config")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(m, dst)
        log("  · mcp.json 覆盖")

    # sidebar 快照：按 id 合并，不用覆盖 ------------------------------------------------------------------
    sidebar_src = src / "sidebar-snapshot.json"
    if sidebar_src.is_file() and uid_dir:
        dst = uid_dir / "sidebar-list-snapshot.json"
        try:
            remote = json.loads(sidebar_src.read_text(encoding="utf-8"))
            local_snap = json.loads(dst.read_text(encoding="utf-8")) if dst.is_file() else {"items": []}
            merged = merge_sidebar(local_snap, remote)
            if apply:
                backup_path(dst, "config")
                dst.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
            log(f"  · sidebar 快照合并：本机 {len(local_snap.get('items', []))} + "
                f"仓库 {len(remote.get('items', []))} → {len(merged.get('items', []))} 条")
        except Exception as ex:
            log(f"  ! sidebar 合并失败：{ex}")
            report.append(f"sidebar: 合并失败 {ex}")

    # 置顶与偏好：置顶合并，其余覆盖
    for name, sub in (("conversations.json", "global"), ("preferences.json", "global"),
                      ("skill-picker.json", "global")):
        s = src / name
        if not (s.is_file() and storage_dir):
            continue
        dst = storage_dir / sub / name
        if name == "conversations.json" and dst.is_file():
            try:
                a = json.loads(dst.read_text(encoding="utf-8"))
                b = json.loads(s.read_text(encoding="utf-8"))
                seen = {x["id"] for x in a.get("pinned", [])}
                for x in b.get("pinned", []):
                    if x["id"] not in seen:
                        a.setdefault("pinned", []).append(x)
                if apply:
                    backup_path(dst, "config")
                    dst.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
                log(f"  · 置顶合并 → {len(a.get('pinned', []))} 条")
            except Exception as ex:
                log(f"  ! 置顶合并失败：{ex}")
        else:
            if apply:
                backup_path(dst, "config")
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, dst)
            log(f"  · {name} 覆盖")


def merge_sidebar(local_snap, remote_snap):
    """按 id 取并集，lastActivityAt 大者胜。"""
    items = {}
    for it in local_snap.get("items", []):
        items[it["id"]] = it
    for it in remote_snap.get("items", []):
        cur = items.get(it["id"])
        if not cur:
            items[it["id"]] = it
        else:
            if (it.get("lastActivityAt") or 0) > (cur.get("lastActivityAt") or 0):
                # 保留本机的置顶意图，其余取新
                merged = dict(it)
                merged["isPinned"] = cur.get("isPinned", it.get("isPinned", False))
                items[it["id"]] = merged
    out = dict(local_snap)
    out["items"] = sorted(items.values(),
                          key=lambda x: x.get("lastActivityAt") or 0, reverse=True)
    out["savedAt"] = int(datetime.now().timestamp() * 1000)
    return out


# ─────────────────────────── L2 会话层 ───────────────────────────

def import_sessions_index(cfg, apply, report, exclude_ids=None):
    idx_file = DATA / "sessions" / "index.jsonl"
    if not idx_file.is_file():
        return
    exclude_ids = exclude_ids or set()
    local = cfg["_local"]
    db = Path(local["workbuddy_home"]) / "workbuddy.db"
    if not db.is_file():
        log("  ! 本机 workbuddy.db 不存在，跳过会话索引")
        return

    records = [json.loads(l) for l in idx_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    before = len(records)
    records = [r for r in records if r.get("id") not in exclude_ids]
    if before != len(records):
        log(f"  · 按选择跳过 {before - len(records)} 条会话索引")

    # 路径重写
    remapped = unmapped = 0
    for r in records:
        new_cwd, state = remap_path(r.get("cwd"), r.get("_host", ""), cfg)
        if state == "remapped":
            remapped += 1
        elif state == "unmapped":
            unmapped += 1
            report.append(f"session {r['id'][:8]}: cwd 无法映射 {r.get('cwd')}")
        r["_cwd_new"] = new_cwd

    try:
        conn = sqlite3.connect(db, timeout=15)
        conn.row_factory = sqlite3.Row
        cols = {d[1] for d in conn.execute("PRAGMA table_info(sessions)")}
        need = {"id", "cwd", "title", "status", "updated_at"}
        if not need.issubset(cols):
            log(f"  ! schema 不匹配，缺少字段 {need - cols}，中止写库")
            report.append("sessions: schema 不匹配，未写库")
            conn.close()
            return

        exist = {row[0]: dict(row) for row in conn.execute("SELECT * FROM sessions")}
        ins = upd = skip = 0
        for r in records:
            sid = r["id"]
            if sid not in exist:
                ins += 1
                if apply:
                    fields = [k for k in r if k in cols and not k.startswith("_")]
                    vals = []
                    for k in fields:
                        v = r[k]
                        if k == "cwd":
                            v = r["_cwd_new"]
                        if k == "status" and v == "running":
                            v = "interrupted"
                        vals.append(v)
                    sql = (f"INSERT INTO sessions ({','.join(fields)}) "
                           f"VALUES ({','.join('?' * len(fields))})")
                    conn.execute(sql, vals)
            else:
                cur = exist[sid]
                if (r.get("updated_at") or 0) > (cur.get("updated_at") or 0):
                    upd += 1
                    if apply:
                        conn.execute(
                            "UPDATE sessions SET title=?, custom_title=?, status=?, updated_at=? WHERE id=?",
                            (r.get("title"), r.get("custom_title"),
                             "interrupted" if r.get("status") == "running" else r.get("status"),
                             r.get("updated_at"), sid))
                else:
                    skip += 1
        if apply:
            conn.commit()
        conn.close()
        log(f"  · 会话索引：新增 {ins}，更新 {upd}，本机较新跳过 {skip}")
        log(f"  · 路径重写：改写 {remapped}，无法映射 {unmapped}")
    except Exception as ex:
        log(f"  ! 写库失败：{ex}")
        report.append(f"sessions: 写库失败 {ex}")


def import_transcripts(cfg, apply, report, exclude_ids=None):
    src_root = DATA / "sessions" / "transcripts"
    if not src_root.is_dir():
        return
    exclude_ids = exclude_ids or set()
    local = cfg["_local"]
    home = Path(local["workbuddy_home"])
    projects = home / "projects"
    projects.mkdir(parents=True, exist_ok=True)

    # 用索引里的 cwd 重算目标目录名
    cwd_map = {}
    idx = DATA / "sessions" / "index.jsonl"
    if idx.is_file():
        for line in idx.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            cwd_map[r["id"]] = r.get("_cwd_new") or r.get("cwd") or ""

    n_new = n_skip = 0
    for f in src_root.rglob("*.jsonl"):
        sid = f.stem
        if sid in exclude_ids:
            continue
        cwd = cwd_map.get(sid)
        if not cwd:
            # 索引里没有（比如已被排除），放回原目录结构
            dst = projects / f.parent.name / f.name
        else:
            dst = projects / cwd_to_slug(cwd) / f.name
        if dst.exists() and dst.stat().st_size == f.stat().st_size:
            n_skip += 1
            continue
        n_new += 1
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
    log(f"  · 会话正文：写入 {n_new}，已存在跳过 {n_skip}")


# ─────────────────────────── 主流程 ───────────────────────────

def main():
    ap = argparse.ArgumentParser(description="把同步仓库的状态导入本机")
    ap.add_argument("--layer", choices=["l1", "l2", "all"], default="all")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只演练）")
    ap.add_argument("--force", action="store_true", help="WorkBuddy 运行中仍继续")
    ap.add_argument("--exclude", default="",
                    help="逗号分隔的 session id，这些不导入（对应预览里取消勾选）")
    ap.add_argument("--only", default="",
                    help="只导入这些类别：identity,skills,tasks,config,sessions（逗号分隔）")
    args = ap.parse_args()

    exclude_ids = {x.strip() for x in args.exclude.split(",") if x.strip()}
    only = {x.strip() for x in args.only.split(",") if x.strip()} or None

    def want(cat):
        return only is None or cat in only

    cfg = load_config()
    report = []
    apply = args.apply

    log("=" * 60)
    log(f"wb-sync 导入   主机 {cfg['_host']}")
    log("=" * 60)

    running = workbuddy_running()
    if running:
        log("⚠ 检测到 WorkBuddy 正在运行")
        if apply and not args.force and args.layer in ("l2", "all"):
            log("  写数据库需要先完全退出 WorkBuddy。")
            log("  只想恢复文件层的话：python import.py --apply --layer l1")
            sys.exit(2)
        if running and apply and args.force:
            log("  --force 已指定，继续（有损坏数据库的风险）")
        elif running and not apply:
            log("  演练模式不受影响（只读，不写库）")
    log("")

    if args.layer in ("l1", "all"):
        log("[L1 文件层]")
        if apply:
            backup_path(Path(cfg["_local"]["workbuddy_home"]), "home-l1")
        if want("identity"):
            import_identity(cfg, apply, report)
            import_memory(cfg, apply, report)
        dirs = []
        if want("skills"):
            dirs.append("skills")
        if want("sessions") or want("tasks"):
            dirs += ["tasks", "plans"]
        if dirs:
            import_tree_dirs(cfg, apply, report, dirs)
        if want("config"):
            import_config(cfg, apply, report)

    if args.layer in ("l2", "all"):
        log("\n[L2 会话层]")
        if apply and running and not args.force:
            sys.exit(2)
        local = cfg["_local"]
        db = Path(local["workbuddy_home"]) / "workbuddy.db"
        if apply and db.is_file():
            bak = db.with_suffix(f".db.bak.{datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(db, bak)
            log(f"  · 数据库已备份 → {bak.name}")
        if want("sessions"):
            import_sessions_index(cfg, apply, report, exclude_ids)
            import_transcripts(cfg, apply, report, exclude_ids)

    log("\n" + "=" * 60)
    if report:
        log(f"需要留意 {len(report)} 项：")
        for r in report[:30]:
            log(f"  - {r}")
        if len(report) > 30:
            log(f"  … 另有 {len(report) - 30} 项")
    else:
        log("无异常")
    if not apply:
        log("\n这是演练。确认无误后加 --apply 执行。")
    log("=" * 60)


if __name__ == "__main__":
    main()
