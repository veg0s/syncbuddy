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
wb-sync / test_remap.py — 路径重写与目录名编码的回归测试

换机器最怕的就是路径算错：会话能列出来，点进去却是空目录。
这里把两种编码规则都固定下来，改动后先跑这个。

    python test_remap.py
"""

import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("WB_SYNC_REPO") or HERE).resolve()


def load_import_module():
    """import.py 的名字和关键字冲突，只能按路径加载。
    注意用 HERE（脚本所在目录）而不是 ROOT（数据仓库）——脚本已经和数据分开放了。"""
    spec = importlib.util.spec_from_file_location("wbsync_import", HERE / "import.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = load_import_module()
cwd_to_slug = mod.cwd_to_slug
remap_path = mod.remap_path

CFG = {
    "_host": "pc-a",
    "_local": {"workspace_root": "D:/WorkBuddy"},
    "hosts": {
        "pc-a": {"workspace_root": "C:/Users/testuser/WorkBuddy"},
        "pc-b": {"workspace_root": "D:/WorkBuddy"},
    },
}

CASES_SLUG = [
    (r"C:\Users\testuser\WorkBuddy\2026-09-28-12-19-17",
     "c-Users-testuser-WorkBuddy-2026-09-28-12-19-17"),
    ("C:/Users/testuser/WorkBuddy/2026-09-28-12-19-17",
     "c-Users-testuser-WorkBuddy-2026-09-28-12-19-17"),
    (r"C:\Users\testuser\Documents\威尔生物workspace",
     "c-Users-testuser-Documents-威尔生物workspace"),
    ("D:/WorkBuddy/automation-2026-10-16-09-09-45",
     "d-WorkBuddy-automation-2026-10-16-09-09-45"),
]

CASES_REMAP = [
    # (cwd, src_host, 期望结果, 期望状态)
    ("C:/Users/testuser/WorkBuddy/2026-09-28-12-19-17", "pc-a",
     "D:/WorkBuddy/2026-09-28-12-19-17", "remapped"),
    ("C:\\Users\\testuser\\WorkBuddy\\x", "pc-a",
     "D:/WorkBuddy/x", "remapped"),
    ("D:/WorkBuddy/abc", "pc-b",
     "D:/WorkBuddy/abc", "same"),
    ("C:/Users/testuser/Documents/某目录", "pc-a",
     "C:/Users/testuser/Documents/某目录", "unmapped"),
]


def run():
    ok = fail = 0
    print("── cwd → 目录名编码 ──")
    for cwd, expect in CASES_SLUG:
        got = cwd_to_slug(cwd)
        if got == expect:
            ok += 1
            print(f"  ✔ {cwd}")
        else:
            fail += 1
            print(f"  ✘ {cwd}\n     期望 {expect}\n     实际 {got}")

    print("\n── 跨机路径重写 ──")
    for cwd, src, expect, expect_state in CASES_REMAP:
        got, state = remap_path(cwd, src, CFG)
        if got == expect and state == expect_state:
            ok += 1
            print(f"  ✔ [{state}] {cwd} → {got}")
        else:
            fail += 1
            print(f"  ✘ {cwd} (src={src})\n"
                  f"     期望 [{expect_state}] {expect}\n"
                  f"     实际 [{state}] {got}")

    print(f"\n{ok} 通过 / {fail} 失败")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(run())
