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
wb-sync / export.py — 从本机导出 WorkBuddy 状态到仓库

导出的两层内容：
  L1 身份层  SOUL / IDENTITY / USER / MEMORY、memory（age 加密）、skills、tasks、plans、配置
  L2 会话层  sessions 索引（来自 workbuddy.db）+ 会话正文（projects/*.jsonl）

设计要点：
  - 只读源数据，不改动 WorkBuddy 任何文件；WorkBuddy 运行中也可以导出
  - memory 一律加密后落盘，明文不进仓库
  - 每条记录标记来源主机名，供导入时做路径重写与冲突判定
  - 自动生成的会话（is_background_automation=1）默认排除

用法：
    python export.py              导出全部
    python export.py --layer l1   只导身份层（快，几秒）
    python export.py --layer l2   只导会话层
    python export.py --dry-run    只统计不写入
"""

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 仓库位置由启动器通过 WB_SYNC_REPO 传进来；没有就退回脚本目录（兼容旧布局）
ROOT = Path(os.environ.get("WB_SYNC_REPO") or HERE).resolve()
DATA = ROOT / "data"
CONFIG_FILE = ROOT / "config.json"

# 这些目录/文件永不进入同步范围
SKIP_NAMES = {
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    ".DS_Store", "Thumbs.db", "desktop.ini",
}
SKIP_SUFFIX = {".pyc", ".pyo", ".log", ".tmp", ".swp", ".db-wal", ".db-shm"}


# ────────────────────────────── 基础设施 ──────────────────────────────

def log(msg):
    print(msg, flush=True)


def load_config():
    if not CONFIG_FILE.is_file():
        sys.exit(f"缺少配置文件：{CONFIG_FILE}")
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    host = socket.gethostname()
    hcfg = cfg.get("hosts", {}).get(host)
    if not hcfg:
        known = ", ".join(cfg.get("hosts", {}).keys()) or "（空）"
        sys.exit(f"config.json 里没有本机 '{host}' 的配置段。\n已有：{known}\n"
                 f"请复制一段并把路径改成本机实际路径。")
    cfg["_host"] = host
    cfg["_local"] = hcfg
    return cfg


def sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def should_skip(name, path):
    if name in SKIP_NAMES:
        return True
    if path.suffix.lower() in SKIP_SUFFIX:
        return True
    return False


def copy_tree(src: Path, dst: Path, stats, dry=False):
    """递归复制目录，返回复制文件数。跳过缓存与二进制临时文件。"""
    if not src.is_dir():
        return 0
    n = 0
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        if should_skip(item.name, item):
            continue
        rel = item.relative_to(src)
        target = dst / rel
        stats["bytes"] = stats.get("bytes", 0) + item.stat().st_size
        n += 1
        if dry:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
    return n


def age_encrypt(age_bin: str, pubkey: str, src: Path, dst: Path):
    """用 age 公钥加密单文件。已存在则先删，避免 age 拒绝覆盖。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    r = subprocess.run(
        [age_bin, "-r", pubkey, "-o", str(dst), str(src)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"age 加密失败 {src.name}: {r.stderr.strip()}")
    return dst


# ────────────────────────────── L1 身份层 ──────────────────────────────

def export_identity(cfg, stats, dry):
    """身份四件套。优先取 personal 目录（实际注入源），缺失则退回 home 根。"""
    local = cfg["_local"]
    home = Path(local["workbuddy_home"])
    personal = Path(local.get("personal_dir", "")) if local.get("personal_dir") else None
    out = DATA / "identity"
    out.mkdir(parents=True, exist_ok=True)

    names = ["SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md"]
    found = []
    for name in names:
        src = None
        if personal and (personal / name).is_file():
            src = personal / name
        elif (home / name).is_file():
            src = home / name
        if not src:
            log(f"  · {name:14s} 缺失，跳过")
            continue
        found.append((name, src))
        if not dry:
            shutil.copy2(src, out / name)
        stats["bytes"] += src.stat().st_size
        log(f"  · {name:14s} {src.stat().st_size:>7,} B  ← {src.parent.name}")

    return len(found)


def export_memory(cfg, stats, dry):
    """memory 目录：全部 .md 走 age 加密，明文不留。

    age 每次加密用随机 nonce，同一明文也会得到不同密文。若不做处理，
    每次导出都会产生无意义的 diff。这里用明文 sha256 做缓存：
    明文没变就保留已有密文，不重复加密。
    """
    local = cfg["_local"]
    home = Path(local["workbuddy_home"])
    src_dir = home / "memory"
    out = DATA / "memory"
    out.mkdir(parents=True, exist_ok=True)

    # 没配公钥就跳过这一层，别让整个导出崩掉 ——
    # 大量用户只用 SSH + 会话同步，不碰记忆加密
    pubkey = (cfg.get("age_public_key") or "").strip()
    if not pubkey:
        print("  ! 未配置 age 公钥，跳过记忆同步")
        return
    age_bin = local.get("age_binary", "age")
    hash_file = out / ".plain-hashes.json"

    cache = {}
    if hash_file.is_file():
        try:
            cache = json.loads(hash_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    if not src_dir.is_dir():
        log("  · memory 目录不存在，跳过")
        return 0

    files = [p for p in src_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".md"]
    new_cache = {}
    changed = skipped = 0

    for p in files:
        rel = p.relative_to(src_dir).as_posix()
        dst = out / (rel + ".age")
        plain_hash = sha256_of(p)
        new_cache[rel] = plain_hash
        stats["bytes"] += p.stat().st_size

        if cache.get(rel) == plain_hash and dst.is_file():
            skipped += 1
            continue
        changed += 1
        if dry:
            log(f"  · [dry] {rel} 需重新加密")
            continue
        try:
            age_encrypt(age_bin, pubkey, p, dst)
            log(f"  · {rel}  →  {dst.name}  ({dst.stat().st_size:,} B 密文)")
        except Exception as ex:
            log(f"  ! 加密失败 {rel}: {ex}")

    if not dry:
        hash_file.write_text(json.dumps(new_cache, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    if skipped:
        log(f"  · 记忆：{changed} 个重新加密，{skipped} 个未变（复用已有密文）")
    return len(files)


def export_skills(cfg, stats, dry):
    local = cfg["_local"]
    src = Path(local["workbuddy_home"]) / "skills"
    out = DATA / "skills"
    n = copy_tree(src, out, stats, dry)
    log(f"  · skills  {n} 个文件")
    return n


def export_tasks_plans(cfg, stats, dry):
    local = cfg["_local"]
    home = Path(local["workbuddy_home"])
    total = 0
    for name in ("tasks", "plans"):
        src, out = home / name, DATA / name
        n = copy_tree(src, out, stats, dry)
        log(f"  · {name:8s} {n} 个文件")
        total += n
    return total


def export_config(cfg, stats, dry):
    """mcp 配置、sidebar 快照、置顶状态、界面偏好。不含设备绑定与运行态。"""
    local = cfg["_local"]
    home = Path(local["workbuddy_home"])
    uid_dir = Path(local["uid_dir"]) if local.get("uid_dir") else None
    storage_dir = Path(local["storage_dir"]) if local.get("storage_dir") else None
    out = DATA / "config"
    out.mkdir(parents=True, exist_ok=True)

    pairs = [("mcp.json", home / "mcp.json")]
    if uid_dir:
        # 侧边栏任务列表快照（含置顶、状态、空间归属）
        pairs.append(("sidebar-snapshot.json", uid_dir / "sidebar-list-snapshot.json"))
    if storage_dir:
        g = storage_dir / "global"
        pairs.append(("conversations.json", g / "conversations.json"))
        pairs.append(("preferences.json", g / "preferences.json"))
        pairs.append(("skill-picker.json", g / "skill-picker.json"))

    n = 0
    for dst_name, src in pairs:
        if not src.is_file():
            log(f"  · {dst_name:24s} 缺失")
            continue
        if not dry:
            shutil.copy2(src, out / dst_name)
        stats["bytes"] += src.stat().st_size
        log(f"  · {dst_name:24s} {src.stat().st_size:>7,} B")
        n += 1
    return n


def export_automations(cfg, stats, dry):
    """定时任务定义导出为 JSONL。这些是任务定义，不含运行实例。"""
    local = cfg["_local"]
    db = Path(local["workbuddy_home"]) / "workbuddy.db"
    if not db.is_file():
        log("  · workbuddy.db 不存在，跳过 automations")
        return 0

    rows = read_db_rows(db, "SELECT * FROM automations")
    if rows is None:
        return 0

    out = DATA / "config" / "automations.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not dry:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                r["_host"] = cfg["_host"]
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats["bytes"] += out.stat().st_size if out.exists() else 0
    log(f"  · automations  {len(rows)} 条")
    return len(rows)


# ────────────────────────────── L2 会话层 ──────────────────────────────

def read_db_rows(db_path: Path, sql: str):
    """只读方式打开 db，避免与运行中的 WorkBuddy 抢锁。"""
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql)]
        finally:
            conn.close()
    except Exception as ex:
        log(f"  ! 读库失败：{ex}")
        return None


SESSION_FIELDS = [
    "id", "cwd", "user_id", "title", "custom_title", "status",
    "created_at", "updated_at", "deleted_at", "is_playground",
    "source_mode", "model", "mode", "permission_mode", "context_window",
    "thought_level", "group_id", "group_title", "visibility", "unread",
    "is_background_automation", "expert_id", "conversation_origin",
]


def export_sessions_index(cfg, stats, dry):
    """
    会话索引：元数据落 index.jsonl，正文另存 transcripts。

    合并式写入，不是重建：先读入已有 index（可能含 pull 下来的另一台机器的
    会话），本机有的按 id 覆盖，本机没有的原样保留。否则分叉合并时会把
    对方的会话元数据整片冲掉。
    """
    local = cfg["_local"]
    db = Path(local["workbuddy_home"]) / "workbuddy.db"
    if not db.is_file():
        log("  · workbuddy.db 不存在，跳过 sessions")
        return 0

    rows = read_db_rows(db, "SELECT * FROM sessions")
    if rows is None:
        return 0

    excl_bg = cfg.get("exclude_background_automation", True)
    out = DATA / "sessions" / "index.jsonl"

    merged = {}
    if out.is_file():
        for line in out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                old = json.loads(line)
                merged[old["id"]] = old
            except Exception:
                continue

    local_count = kept = 0
    for r in rows:
        local_count += 1
        if excl_bg and r.get("is_background_automation"):
            continue
        item = {k: r.get(k) for k in SESSION_FIELDS}
        item["_host"] = cfg["_host"]
        if item.get("status") == "running":
            item["_was_running"] = True
        merged[item["id"]] = item
        kept += 1

    foreign = len(merged) - kept
    out.parent.mkdir(parents=True, exist_ok=True)
    if not dry:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            for item in merged.values():
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
    stats["bytes"] += out.stat().st_size if out.exists() else 0
    log(f"  · sessions 索引  本机 {kept} 条，另有其他机器 {foreign} 条并入，共 {len(merged)} 条")
    return len(merged)


def export_transcripts(cfg, stats, dry):
    """
    会话正文：projects/<cwd-slug>/<session-id>.jsonl，保持原目录结构。

    增量合并：不删除目标目录里已有的文件（那些可能是从另一台机器拉下来的），
    只补充本机有而目标缺的、或内容已变化的。宁可多留，不可误删。
    """
    local = cfg["_local"]
    src_root = Path(local["workbuddy_home"]) / "projects"
    dst_root = DATA / "sessions" / "transcripts"

    if not src_root.is_dir():
        log("  · projects 目录不存在，跳过正文")
        return 0

    # 允许导出的 session id（排除自动生成的）
    allowed = None
    if cfg.get("exclude_background_automation", True):
        idx = DATA / "sessions" / "index.jsonl"
        if idx.is_file():
            allowed = set()
            for line in idx.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        allowed.add(json.loads(line)["id"])
                    except Exception:
                        pass

    n_new = n_skip = n_excl = 0
    n_seen = 0
    total_bytes = 0
    for p in sorted(src_root.rglob("*.jsonl")):
        if p.name.endswith(".file-rollback.ndjson"):
            continue
        # 首次导出可能上千个文件、好几分钟，隔一段报个数，
        # 否则用户只看到光标不动，以为卡死了
        n_seen += 1
        if n_seen % 200 == 0:
            log(f"  · 已扫描 {n_seen} 个正文文件")
        if allowed is not None and p.stem not in allowed:
            n_excl += 1
            continue
        rel = p.relative_to(src_root)
        dst = dst_root / rel
        size = p.stat().st_size
        if dst.is_file() and dst.stat().st_size == size:
            n_skip += 1
            continue
        n_new += 1
        total_bytes += size
        if dry:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)

    stats["bytes"] += total_bytes
    log(f"  · 会话正文  新增/更新 {n_new} 个，未变 {n_skip} 个"
        + (f"，跳过自动生成 {n_excl} 个" if n_excl else ""))

    # 目标端有但本机没有的（另一台机器的会话）——保留并报数
    if not dry and dst_root.is_dir():
        local_ids = {p.stem for p in src_root.rglob("*.jsonl")
                     if not p.name.endswith(".file-rollback.ndjson")}
        foreign = [p for p in dst_root.rglob("*.jsonl") if p.stem not in local_ids]
        if foreign:
            log(f"  · 保留其他机器独有的正文 {len(foreign)} 个")
    return n_new


# ────────────────────────────── 清单 ──────────────────────────────

# ────────────────────────────── 待同步预扫描 ──────────────────────────────

CATEGORY_LABELS = {
    "identity": "身份与记忆",
    "skills": "技能",
    "config": "配置",
    "sessions": "会话",
}


def _changed(src: Path, dst: Path) -> bool:
    """目标不存在、大小不同、或源比目标新，都算有变化。"""
    if not dst.is_file():
        return True
    if src.stat().st_size != dst.stat().st_size:
        return True
    # copy2 保留 mtime，所以源更晚就是改过；留 1 秒容差避开文件系统精度
    return src.stat().st_mtime > dst.stat().st_mtime + 1


def _scan_tree(src_root: Path, dst_root: Path, limit=300) -> list:
    if not src_root.is_dir():
        return []
    items = []
    for p in src_root.rglob("*"):
        if not p.is_file() or should_skip(p.name, p):
            continue
        rel = p.relative_to(src_root)
        if _changed(p, dst_root / rel):
            items.append({"name": rel.as_posix(), "bytes": p.stat().st_size})
            if len(items) >= limit:
                break
    return items


def plan_upload(cfg):
    """
    只比对不写盘：本机有哪些内容相对 data/ 变了。
    面板的「待上传」页签直接用这个结果。
    """
    local = cfg["_local"]
    home = Path(local["workbuddy_home"])
    personal = Path(local["personal_dir"]) if local.get("personal_dir") else None
    uid_dir = Path(local["uid_dir"]) if local.get("uid_dir") else None
    storage_dir = Path(local["storage_dir"]) if local.get("storage_dir") else None
    result = {}

    # ── 身份与记忆 ──
    items = []
    for name in ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md"):
        src = None
        if personal and (personal / name).is_file():
            src = personal / name
        elif (home / name).is_file():
            src = home / name
        if src and _changed(src, DATA / "identity" / name):
            items.append({"name": name, "bytes": src.stat().st_size})
    mem_src = home / "memory"
    if mem_src.is_dir():
        # 密文和明文大小必然不同，不能拿 .age 的 size 比；用 export_memory
        # 维护的明文 hash 缓存判断
        cache = {}
        hash_file = DATA / "memory" / ".plain-hashes.json"
        if hash_file.is_file():
            try:
                cache = json.loads(hash_file.read_text(encoding="utf-8"))
            except Exception:
                cache = {}
        for p in mem_src.rglob("*.md"):
            rel = p.relative_to(mem_src).as_posix()
            if cache.get(rel) != sha256_of(p):
                items.append({"name": f"记忆/{p.name}", "bytes": p.stat().st_size})
    result["identity"] = {"label": CATEGORY_LABELS["identity"], "items": items}

    # ── 技能 ──
    result["skills"] = {"label": CATEGORY_LABELS["skills"],
                        "items": _scan_tree(home / "skills", DATA / "skills")}

    # ── 配置 ──
    items = []
    cfg_pairs = [("mcp.json", home / "mcp.json")]
    if uid_dir:
        cfg_pairs.append(("sidebar-snapshot.json", uid_dir / "sidebar-list-snapshot.json"))
    if storage_dir:
        g = storage_dir / "global"
        cfg_pairs += [("conversations.json", g / "conversations.json"),
                      ("preferences.json", g / "preferences.json"),
                      ("skill-picker.json", g / "skill-picker.json")]
    for dst_name, src in cfg_pairs:
        if src.is_file() and _changed(src, DATA / "config" / dst_name):
            items.append({"name": dst_name, "bytes": src.stat().st_size})
    result["config"] = {"label": CATEGORY_LABELS["config"], "items": items}

    # ── 会话（索引 + 正文合并，一个会话一行）──
    # 之前的写法把「索引变更」和「正文变更」当两条列，
    # 同一个会话会出现两次（一次标题、一次 id 前缀），列表里看着像一堆陌生会话。
    db = home / "workbuddy.db"
    known = {}
    idx_file = DATA / "sessions" / "index.jsonl"
    if idx_file.is_file():
        for line in idx_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    known[r["id"]] = r.get("updated_at") or 0
                except Exception:
                    pass

    # 只保留「主会话」：定时任务和子 agent 不单独列出，
    # 它们的正文跟着主会话走，用户不需要在列表里看到
    excl_bg = cfg.get("exclude_background_automation", True)
    session_meta = {}
    rows = read_db_rows(
        db, "SELECT id, title, updated_at, is_background_automation, conversation_origin "
            "FROM sessions")
    for r in rows or []:
        if excl_bg and r.get("is_background_automation"):
            continue
        if (r.get("conversation_origin") or "") == "agents":
            continue
        session_meta[r["id"]] = r

    changed = {}
    for sid, r in session_meta.items():
        if (r.get("updated_at") or 0) > known.get(sid, -1):
            changed.setdefault(sid, {})["index"] = True

    src_root = home / "projects"
    dst_root = DATA / "sessions" / "transcripts"
    if src_root.is_dir():
        for p in src_root.rglob("*.jsonl"):
            if p.name.endswith(".file-rollback.ndjson"):
                continue
            sid = p.stem
            if sid not in session_meta:
                continue        # 定时任务 / 子 agent 的正文，不列
            if _changed(p, dst_root / p.relative_to(src_root)):
                e = changed.setdefault(sid, {})
                e["body"] = True
                e["bytes"] = p.stat().st_size

    items = []
    for sid, e in changed.items():
        tag = []
        if e.get("index"):
            tag.append("信息")
        if e.get("body"):
            tag.append("对话")
        items.append({
            "name": session_meta[sid].get("title") or "（无标题会话）",
            "key": sid,
            "bytes": e.get("bytes", 0),
            "detail": "更新" + "+".join(tag) if tag else "有变化",
        })

    # 会话内任务清单与 plan 文档：跟着会话走，不单独成类
    side = []
    for sub in ("tasks", "plans"):
        side += _scan_tree(home / sub, DATA / sub)
    if side:
        items.append({
            "name": f"会话内任务与计划（{len(side)} 个文件）",
            "key": "__side__",
            "bytes": sum(x["bytes"] for x in side),
            "detail": "随会话同步",
        })

    result["sessions"] = {"label": CATEGORY_LABELS["sessions"], "items": items}

    total = sum(len(v["items"]) for v in result.values())
    return {"categories": result, "total": total,
            "generated_at": int(time.time())}


def write_manifest(cfg, dry):
    """记录每个数据文件的 sha256，供导入时校验与冲突检测。

    注意：必须排除 manifest.json 自身。它每次生成内容都不同，
    若把自己算进去，下次校验必然不符（自引用）。
    """
    if dry:
        return
    entries = []
    total = 0
    for p in sorted(DATA.rglob("*")):
        if not p.is_file():
            continue
        if p.name == "manifest.json" and p.parent == DATA:
            continue
        size = p.stat().st_size
        total += size
        entries.append({
            "path": p.relative_to(DATA).as_posix(),
            "size": size,
            "sha256": sha256_of(p),
        })
    manifest_file = DATA / "manifest.json"
    # 幂等：内容没变就不重写，否则每次导出都会刷新时间戳产生无意义 diff
    if manifest_file.is_file():
        try:
            old = json.loads(manifest_file.read_text(encoding="utf-8"))
            if old.get("files") == entries:
                log("\n清单内容无变化，跳过重写")
                return
        except Exception:
            pass

    manifest = {
        "version": 1,
        "host": cfg["_host"],
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file_count": len(entries),
        "total_bytes": total,
        "files": entries,
    }
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"\n清单：{len(entries)} 个文件，{total / 1e6:.1f} MB")


# ────────────────────────────── 主流程 ──────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="导出 WorkBuddy 状态到同步仓库")
    ap.add_argument("--layer", choices=["l1", "l2", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写盘")
    ap.add_argument("--plan", action="store_true",
                    help="只预扫描：输出待上传清单（JSON），不写盘")
    ap.add_argument("--only", default="",
                    help="只导出这些类别：identity,skills,tasks,config,sessions（逗号分隔）")
    args = ap.parse_args()

    cfg = load_config()

    # 预扫描：给面板的「待上传」用
    if args.plan:
        # 关键：写 bytes 而不是 print。
        # Windows 上 stdout 接管道时 Python 用 locale 编码（cp936），
        # 父进程按 utf-8 解码就会得到一堆 �。
        out = json.dumps(plan_upload(cfg), ensure_ascii=False)
        sys.stdout.buffer.write(out.encode("utf-8"))
        sys.stdout.buffer.flush()
        return

    host = cfg["_host"]
    dr = args.dry_run
    only = {x.strip() for x in args.only.split(",") if x.strip()} or None

    def want(cat):
        return only is None or cat in only

    t0 = time.time()

    log("=" * 60)
    log(f"wb-sync 导出   主机 {host}" + ("   [DRY-RUN]" if dr else "")
        + (f"   [仅 {','.join(sorted(only))}]" if only else ""))
    log("=" * 60)

    DATA.mkdir(parents=True, exist_ok=True)
    stats = {"bytes": 0}

    if args.layer in ("l1", "all"):
        log("\n[L1 身份层]")
        if want("identity"):
            export_identity(cfg, stats, dr)
            export_memory(cfg, stats, dr)
        if want("skills"):
            export_skills(cfg, stats, dr)
        # 会话内任务清单与 plan 文档跟着会话一起走
        if want("tasks") or want("sessions"):
            export_tasks_plans(cfg, stats, dr)
        if want("config"):
            export_config(cfg, stats, dr)
            export_automations(cfg, stats, dr)

    if args.layer in ("l2", "all"):
        log("\n[L2 会话层]")
        if want("sessions"):
            export_sessions_index(cfg, stats, dr)
            export_transcripts(cfg, stats, dr)

    write_manifest(cfg, dr)

    log(f"\n完成，耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
