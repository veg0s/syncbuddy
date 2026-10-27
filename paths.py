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
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "wbsync"

_REPO_NAMES = ("wb-sync", "workwork", "workwork-sync", "repo")


def _looks_like_repo(p: Path) -> bool:
    return (p / "config.json").is_file() or (p / "data").is_dir()


def repo_root() -> Path:
    env = os.environ.get("WB_SYNC_REPO", "").strip()
    if env:
        # 环境变量显式指定就采信，哪怕路径暂时不存在（报错时能看出配错了）
        return Path(env).expanduser().resolve()

    for name in _REPO_NAMES:
        cand = HERE.parent / name
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
