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
WorkBuddy app.asar 探测工具

用途：查看 WorkBuddy 主程序内部的模块与接口，用于判断某项能力
（云端同步、记忆、任务列表、技能上传等）是否存在、当前处于什么状态。

典型场景：WorkBuddy 版本升级后跑一次 `check`，看官方有没有放开
跨设备同步的能力位，避免自己重复造轮子。

用法：
    python asar_probe.py ls      [名字正则]          列出 asar 内文件
    python asar_probe.py show    <路径> [字符数]     打印某个文件内容
    python asar_probe.py grep    <正则> [文件正则] [上下文] [最大条数]
    python asar_probe.py paths   [过滤正则]          列出所有接口路径
    python asar_probe.py check                       云能力位体检

注意：
  - asar 头部里部分条目的 offset/size 是字符串，必须 int()；
    unpacked 的条目没有 offset，访问前要跳过。
  - 超过 30MB 的 bundle 默认跳过（多为第三方依赖），可用 --all 强制。
  - 默认从环境变量 WB_ASAR 或常见安装位置找 app.asar，
    装在别处时用 --asar <路径> 指定。
"""

import json
import os
import re
import struct
import sys

def _find_asar() -> str:
    """
    定位 app.asar，不写死任何人的安装路径。

    顺序：环境变量 WB_ASAR → 常见安装位置。
    找不到就返回一个候选路径，让报错信息具体（提示用 --asar 指定）。
    """
    env = os.environ.get("WB_ASAR", "").strip()
    if env:
        return env

    local = os.environ.get("LOCALAPPDATA", "")
    cands = []
    if local:
        cands.append(os.path.join(local, "Programs", "WorkBuddy",
                                  "resources", "app.asar"))
    cands += [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "WorkBuddy", "resources", "app.asar"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     "WorkBuddy", "resources", "app.asar"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[0] if cands else "app.asar"


def _product_json(asar_path: str) -> str:
    """product.cloudhosted.json 总在 asar 同级的 unpacked/cli/ 下。"""
    return os.path.join(os.path.dirname(asar_path),
                        "app.asar.unpacked", "cli", "product.cloudhosted.json")


DEFAULT_ASAR = _find_asar()

MAX_SCAN_BYTES = 30_000_000


class Asar:
    def __init__(self, path=DEFAULT_ASAR):
        if not os.path.isfile(path):
            raise SystemExit(f"找不到 asar：{path}\n可用 --asar 指定其它路径")
        self.path = path
        self._f = open(path, "rb")
        a, b, c, e = struct.unpack("<4I", self._f.read(16))
        self._f.seek(16)
        raw = self._f.read(c + 8).decode("utf-8", errors="replace")
        self.hdr = json.JSONDecoder().raw_decode(raw)[0]
        self._base = 8 + b
        self.files = {}
        self._walk(self.hdr, "")

    def _walk(self, node, prefix):
        for k, v in node.get("files", {}).items():
            p = f"{prefix}/{k}" if prefix else k
            if "files" in v:
                self._walk(v, p)
            else:
                self.files[p] = v

    def size(self, name):
        try:
            return int(self.files[name].get("size", 0))
        except (TypeError, ValueError):
            return 0

    def read(self, name, limit=None):
        meta = self.files.get(name)
        if not meta or "offset" not in meta or "size" not in meta:
            return None
        size = int(meta["size"])
        if limit is not None:
            size = min(size, limit)
        self._f.seek(self._base + int(meta["offset"]))
        return self._f.read(size).decode("utf-8", errors="replace")

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass


def cmd_ls(asar, pattern):
    rx = re.compile(pattern, re.I) if pattern else None
    n = 0
    for name in sorted(asar.files):
        if rx and not rx.search(name):
            continue
        print(f"{asar.size(name):>10}  {name}")
        n += 1
    print(f"\n共 {n} 项")


def cmd_show(asar, path, limit):
    t = asar.read(path, limit)
    if t is None:
        print(f"未找到：{path}\n提示：用 ls 子命令确认路径")
        return
    print(t)


def cmd_grep(asar, pattern, pathpat, ctx, maxhit, scan_all):
    rx = re.compile(pattern)
    prx = re.compile(pathpat, re.I) if pathpat else None
    hits = 0
    for name in sorted(asar.files):
        if prx and not prx.search(name):
            continue
        if not scan_all and asar.size(name) > MAX_SCAN_BYTES:
            continue
        t = asar.read(name)
        if not t:
            continue
        for m in rx.finditer(t):
            s = max(0, m.start() - ctx)
            frag = t[s:m.end() + ctx].replace("\n", " ")
            print(f"[{name}] …{frag}…")
            hits += 1
            if hits >= maxhit:
                print(f"\n已达上限 {maxhit} 条")
                return
    if hits == 0:
        print("无命中")


def cmd_paths(asar, filt):
    rx = re.compile(r"/(?:[a-z0-9]+/)?api/[a-zA-Z0-9_/\-]{2,60}")
    frx = re.compile(filt, re.I) if filt else None
    found = {}
    for name in sorted(asar.files):
        if not name.endswith(".js") or asar.size(name) > MAX_SCAN_BYTES:
            continue
        t = asar.read(name)
        if not t:
            continue
        for m in rx.finditer(t):
            p = m.group(0).rstrip("-/")
            found.setdefault(p, set()).add(name)
    print(f"接口路径 {len(found)} 条\n")
    for p in sorted(found):
        if frx and not frx.search(p):
            continue
        print(f"  {p:<58} {len(found[p])} 处")


CAPABILITY_PROBES = [
    ("云端长期记忆 API",
     r"/api/memory/(?:global/)?[a-z_]+",
     "记忆画像已上云，跨设备一致"),
    ("任务列表 IM 中转",
     r"/im/api/buddy/(?:c2c/)?tasks",
     "列表元数据可中转，依赖在线主机"),
    ("会话正文回放",
     r"/api/v1/sessions/[^`]*replay",
     "endpoint 取自本机 sidecar"),
    ("会话导入云空间",
     r"/space/api/view/import/conversation",
     "手动、单向、只读"),
    ("专家/企业技能云同步",
     r"ExpertCloudSync(?:Service)?",
     "引擎存在，作用域不含个人 skills"),
    ("个人技能云同步",
     r"(?:personalSkill|userSkill)[A-Za-z]*Cloud",
     "若命中说明官方开放了个人层同步"),
]


def cmd_check(asar):
    print("=" * 66)
    print("WorkBuddy 云端能力位体检")
    print("=" * 66)
    print(f"asar : {asar.path}")
    print(f"大小 : {os.path.getsize(asar.path):,} 字节")
    try:
        pkg = json.loads(asar.read("package.json") or "{}")
        print(f"版本 : {pkg.get('version', '未知')}   electron {pkg.get('electronVersion', '')}")
    except Exception:
        pass
    print()

    blobs = {}
    for name in asar.files:
        if not name.endswith(".js") or asar.size(name) > MAX_SCAN_BYTES:
            continue
        t = asar.read(name)
        if t:
            blobs[name] = t

    for label, pattern, note in CAPABILITY_PROBES:
        rx = re.compile(pattern)
        hit_files, hit_paths = [], set()
        for name, t in blobs.items():
            for m in rx.finditer(t):
                hit_files.append(name)
                hit_paths.add(m.group(0))
                break
        mark = "[✔]" if hit_files else "[ ]"
        print(f"{mark} {label:<22} {note}")
        if hit_files:
            sample = sorted(hit_paths)[:4]
            print(f"     接口/标识: {', '.join(sample) if sample else '（标识符命中）'}")
            print(f"     出现于: {hit_files[0]}" + (f" 等 {len(hit_files)} 个文件" if len(hit_files) > 1 else ""))
        print()

    # 产品特性开关
    print("-" * 66)
    print("产品能力开关（product.cloudhosted.json）")
    print("-" * 66)
    pj = _product_json(asar.path)
    if os.path.isfile(pj):
        try:
            with open(pj, encoding="utf-8") as f:
                pf = json.load(f).get("productFeatures", {})
            keys = ["CloudAgentsSmartMerge", "SkillManage", "ScheduledTasks",
                    "ProjectLocalTask", "DisableMemoryPersonalization", "Connector"]
            for k in keys:
                if k in pf:
                    v = pf[k]
                    mark = "[开]" if v is True else ("[关]" if v is False else "[  ]")
                    print(f"  {mark} {k:<28} {v}")
            others = [k for k in pf if k not in keys and "Sync" in k]
            for k in others:
                print(f"  [  ] {k:<28} {pf[k]}")
        except Exception as ex:
            print(f"  解析失败：{ex}")
    else:
        print("  未找到（可能更换了安装形态）")

    print()
    print("-" * 66)
    print("解读：前 5 项命中属既有能力；第 6 项若由 [ ] 变 [✔]，")
    print("说明官方放开了个人层同步，此时应停用自建方案。")
    print("-" * 66)


def main(argv):
    global DEFAULT_ASAR
    args = [a for a in argv[1:]]
    scan_all = "--all" in args
    args = [a for a in args if a != "--all"]
    if "--asar" in args:
        i = args.index("--asar")
        DEFAULT_ASAR = args[i + 1]
        del args[i:i + 2]

    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    cmd, rest = args[0], args[1:]
    asar = Asar(DEFAULT_ASAR)
    try:
        if cmd == "ls":
            cmd_ls(asar, rest[0] if rest else None)
        elif cmd == "show":
            cmd_show(asar, rest[0], int(rest[1]) if len(rest) > 1 else None)
        elif cmd == "grep":
            cmd_grep(asar,
                     rest[0],
                     rest[1] if len(rest) > 1 else None,
                     int(rest[2]) if len(rest) > 2 else 60,
                     int(rest[3]) if len(rest) > 3 else 40,
                     scan_all)
        elif cmd == "paths":
            cmd_paths(asar, rest[0] if rest else None)
        elif cmd == "check":
            cmd_check(asar)
        else:
            print(f"未知子命令：{cmd}\n")
            print(__doc__)
            return 1
    finally:
        asar.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
