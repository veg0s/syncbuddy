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
syncbuddy / paths.py — 路径解析的唯一来源

开源后别人的目录结构不会和你一样，所以这里不写死任何绝对路径。

仓库位置的解析顺序：
  1. 环境变量 WB_SYNC_REPO（打包成 exe 后用这个指定最方便）
  2. 启动器同级的 wb-sync/
  3. 启动器同级的 workwork/（别人可能这么命名）
  4. 启动器目录下的 repo/
  解析不到就返回第 2 个作为默认值 —— 让报错信息指向一个具体路径，
  而不是抛一个含糊的异常。
"""

import os
import sys
from pathlib import Path

# 源码运行时 __file__ 就在项目目录；打包成 exe 之后它指向 _internal/，
# 真正的程序目录得看 sys.executable。两者分开，仓库定位才不会找错地方。
HERE = Path(__file__).resolve().parent
APP_DIR = (Path(sys.executable).resolve().parent
           if getattr(sys, "frozen", False) else HERE)
TOOLS = HERE / "wbsync"

_REPO_NAMES = ("wb-sync", "workwork", "workwork-sync", "repo")


def _looks_like_repo(p: Path) -> bool:
    return (p / "config.json").is_file() or (p / "data").is_dir()


def repo_root() -> Path:
    env = os.environ.get("WB_SYNC_REPO", "").strip()
    if env:
        # 环境变量显式指定就采信，哪怕路径暂时不存在（报错时能看出配错了）
        return Path(env).expanduser().resolve()

    # 先看程序目录里面（绿色版把仓库放在 exe 旁边），再看上一级
    for base in (APP_DIR, APP_DIR.parent):
        for name in _REPO_NAMES:
            cand = base / name
            if _looks_like_repo(cand):
                return cand.resolve()

    for name in _REPO_NAMES:
        cand = HERE / name
        if _looks_like_repo(cand):
            return cand.resolve()

    return (HERE.parent / "wb-sync").resolve()


WB_SYNC = repo_root()
CONFIG_FILE = WB_SYNC / "config.json"
DATA_DIR = WB_SYNC / "data"


def remote_host(url: str) -> str:
    """
    从 git remote 地址里取出主机名。

    支持这几种写法：
        git@github.com:user/repo.git          → github.com
        ssh://git@gitlab.com:2222/u/r.git     → gitlab.com
        https://gitee.com/user/repo.git       → gitee.com
        git://example.com/repo                → example.com
    拿不到就返回空串。
    """
    if not url:
        return ""
    u = url.strip()

    # 形如 git@host:path
    if "@" in u and "://" not in u:
        tail = u.split("@", 1)[1]
        host = tail.split(":", 1)[0]
        return host.strip()

    # 带协议
    if "://" in u:
        rest = u.split("://", 1)[1]
        if "@" in rest:
            rest = rest.split("@", 1)[1]
        host = rest.split("/", 1)[0].split(":", 1)[0]
        return host.strip()

    return ""


def remote_label(url: str) -> str:
    """给界面用的短标识：优先主机名。"""
    h = remote_host(url)
    if h:
        return h
    return (url or "").strip()[:32] or "远程仓库"


_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def normalize_proxy(url: str):
    """
    规范化代理地址，返回 (地址, 提示文字或空串)。

    Clash / v2rayN 这类本地代理监听的是 **HTTP 代理**端口（走 CONNECT 隧道
    转发 HTTPS 流量），不是 HTTPS 代理。填成 `https://127.0.0.1:7890`
    会让客户端用 TLS 去连代理本身，握手直接失败 —— git 会报
    "Proxy CONNECT aborted"、urllib 报 SSL 错误，看着像网络不通。

    这里只对本地/私网地址做纠正，外网的 HTTPS 代理是合法配置，不动。
    """
    u = (url or "").strip().rstrip("/")
    if not u.startswith("https://"):
        return u, ""

    host = u[8:].split("/")[0].split(":")[0].lower()
    is_local = (host in _LOCAL_HOSTS
                or host.startswith("192.168.")
                or host.startswith("10.")
                or host.startswith("172.16.") or host.startswith("172.17.")
                or host.startswith("172.18.") or host.startswith("172.19.")
                or host.startswith("172.2") or host.startswith("172.3"))
    if not is_local:
        return u, ""

    return ("http://" + u[8:],
            "本地代理是 HTTP 代理，不是 HTTPS —— 地址已自动改成 http://")
