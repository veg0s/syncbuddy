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
syncbuddy / preview.py — 会话差异预览

目的：拉取之前告诉你「另一台机器多了哪几个任务」，每条给标题 + 开场 120 字。

摘要从会话 jsonl 的第一条真实用户消息里提取。难点在于那条消息里塞满了
系统注入块（system-reminder / user_info / identity_context / 文件引用），
真正的用户输入往往只占一小段。所以要先把注入剥掉再截断。

单独运行可以看效果：
    python preview.py                对比本地 db 与仓库 index
    python preview.py --remote       从 origin/main 读（不 pull）
"""

import json
import re
import sqlite3
import _proc
import paths
import sys
from pathlib import Path

WB_SYNC = paths.WB_SYNC
SUMMARY_LEN = 120

# 需要整块剥掉的注入
BLOCK_PATTERNS = [
    r"<system-reminder[\s\S]*?</system-reminder>",
    r"<user_info>[\s\S]*?</user_info>",
    r"<identity_context>[\s\S]*?</identity_context>",
    r"<project_context>[\s\S]*?</project_context>",
    r"<user_references>[\s\S]*?</user_references>",
    r"<memory>[\s\S]*?</memory>",
    r"<attached_files>[\s\S]*?</attached_files>",
    r"<connector-status>[\s\S]*?</connector-status>",
    r"<wecom_routing>[\s\S]*?</wecom_routing>",
]


# 用户消息里的引用语法：
#   @"C:\path"      @ima:"知识库"      @scene#50:"深度研究"      @skill:xxx
REF_PATTERN = re.compile(
    r'@([\w\-]*)(?:#\d+)?\s*:\s*"([^"]*)"'   # 带引号的引用
    r'|@"([^"]*)"'                            # 裸文件路径
    r'|@([\w\-]+):([\w\-\.]+)'                # 不带引号的 @skill:name
)


def strip_injection(text: str):
    r"""
    剥掉系统注入与引用语法，返回 (干净文本, 引用列表)。

    引用语法必须剥掉：用户经常只发一个文件或知识库引用就开口，
    @"C:\...\很长的路径.docx" 会吃掉上百字符，把真实需求挤出截断范围。
    """
    if not text:
        return "", []

    refs = []

    def _drop_ref(m):
        kind = m.group(1) or m.group(4) or ""
        val = m.group(2) or m.group(3) or m.group(5) or ""
        refs.append(f"{kind}:{val}" if kind else val)
        return " "

    # 1. 优先抓 <user_query>，这是最干净的用户原话
    for tag in ("user_query", "original_text"):
        m = re.search(rf"<{tag}>([\s\S]*?)</{tag}>", text)
        if m:
            body = m.group(1)
            body = re.sub(r"<!\[CDATA\[([\s\S]*?)\]\]>", r"\1", body)
            body = REF_PATTERN.sub(_drop_ref, body)
            body = re.sub(r"<[^>]{0,80}>", " ", body)
            cleaned = re.sub(r"\s+", " ", body).strip()
            if cleaned:
                return cleaned, refs

    # 2. 没有 user_query 就整块剥注入
    out = text
    for pat in BLOCK_PATTERNS:
        out = re.sub(pat, " ", out, flags=re.I)
    out = REF_PATTERN.sub(_drop_ref, out)
    out = re.sub(r"<[^>]{0,80}>", " ", out)
    out = out.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", out).strip(), refs


def extract_summary_from_lines(lines, limit=SUMMARY_LEN) -> str:
    """
    从 jsonl 各行里找第一条有意义的用户输入。
    找不到正文时退回「引用了 xxx」，至少能看出这个会话在干嘛。
    """
    fallback = ""
    for raw in lines:
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if rec.get("type") != "message" or rec.get("role") != "user":
            continue
        content = rec.get("content")
        texts = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("input_text", "text"):
                    texts.append(part.get("text", ""))
        for t in texts:
            cleaned, refs = strip_injection(t)
            # 太短的多半是「继续」「好的」，跳到下一条
            if len(cleaned) >= 12:
                return cleaned[:limit]
            if refs and not fallback:
                names = []
                for r in refs[:3]:
                    base = r.replace("\\", "/").rstrip("/").split("/")[-1]
                    names.append(base or r)
                fallback = f"（引用了 {'、'.join(names)}）"
    return fallback[:limit]


def extract_summary_from_file(path: Path, limit=SUMMARY_LEN) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return extract_summary_from_lines(f, limit)
    except Exception:
        return ""


def local_session_ids(workbuddy_home: Path) -> set:
    db = workbuddy_home / "workbuddy.db"
    if not db.is_file():
        return set()
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
        ids = {r[0] for r in conn.execute("SELECT id FROM sessions")}
        conn.close()
        return ids
    except Exception:
        return set()


def read_index_from_repo(data_dir: Path):
    idx = data_dir / "sessions" / "index.jsonl"
    if not idx.is_file():
        return []
    out = []
    for line in idx.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def read_index_from_git(repo: Path, ref="origin/main"):
    """不 pull，直接从远程分支读 index.jsonl。"""
    r = _proc.run(["git", "show", f"{ref}:data/sessions/index.jsonl"],
                       cwd=repo, capture_output=True)
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.decode("utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def git_transcript_map(repo: Path, ref="origin/main") -> dict:
    """session id → git 路径。"""
    r = _proc.run(["git", "ls-tree", "-r", "--name-only", ref,
                        "data/sessions/transcripts"],
                       cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    out = {}
    for line in r.stdout.splitlines():
        if line.endswith(".jsonl"):
            out[Path(line).stem] = line
    return out


def summary_from_git(repo: Path, git_path: str, ref="origin/main", limit=SUMMARY_LEN) -> str:
    r = _proc.run(["git", "show", f"{ref}:{git_path}"],
                       cwd=repo, capture_output=True)
    if r.returncode != 0:
        return ""
    text = r.stdout.decode("utf-8", errors="replace")
    return extract_summary_from_lines(text.splitlines(), limit)


def build_preview(cfg, use_remote=True, limit=SUMMARY_LEN):
    """
    返回 (新增会话列表, 统计)。新增 = 仓库有、本机 db 没有。
    每条：{id, title, summary, cwd, updated_at, host}
    """
    repo = WB_SYNC
    local_cfg = cfg["_local"]
    home = Path(local_cfg["workbuddy_home"])

    remote_idx = read_index_from_git(repo) if use_remote else read_index_from_repo(WB_SYNC / "data")
    if not remote_idx:
        remote_idx = read_index_from_repo(WB_SYNC / "data")
    # index.jsonl 是纯文件、没有主键约束，并发或重试可能写入重复行；
    # 下游一律按 id 去重，取后者（通常更新）。
    remote_idx = list({r["id"]: r for r in remote_idx}.values())

    have = local_session_ids(home)
    fresh = [r for r in remote_idx if r.get("id") not in have]
    fresh.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)

    tmap = git_transcript_map(repo) if use_remote else {}
    data_dir = WB_SYNC / "data" / "sessions" / "transcripts"

    items = []
    for r in fresh:
        sid = r["id"]
        if use_remote and sid in tmap:
            summary = summary_from_git(repo, tmap[sid])
        else:
            hits = list(data_dir.rglob(f"{sid}.jsonl"))
            summary = extract_summary_from_file(hits[0]) if hits else ""
        items.append({
            "id": sid,
            "title": (r.get("custom_title") or r.get("title") or "（无标题）").strip(),
            "summary": summary or "（无正文）",
            "cwd": r.get("cwd", ""),
            "updated_at": r.get("updated_at") or 0,
            "host": r.get("_host", ""),
        })

    return items, {"remote_total": len(remote_idx), "local_total": len(have),
                   "new_count": len(items)}


def fmt_preview(items, stat):
    if not items:
        print(f"本机已有 {stat['local_total']} 个会话，仓库 {stat['remote_total']} 个，无新增。")
        return
    print(f"仓库比本机多 {stat['new_count']} 个会话：\n")
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it['title']}")
        print(f"     {it['summary']}")
        print()


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location("wbcore", WB_SYNC / "import.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = mod.load_config()

    use_remote = "--remote" in sys.argv
    items, stat = build_preview(cfg, use_remote=use_remote)
    fmt_preview(items, stat)


if __name__ == "__main__":
    main()
