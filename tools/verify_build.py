#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2026 Veg0s
#
"""
打包产物冒烟测试。

    python tools/verify_build.py [exe 路径]

默认检查 dist/SyncBuddy/SyncBuddy.exe，通过 `--run` 入口跑 test_remap，
确认打包后同步引擎还能被调用。

为什么用 Python 而不是 PowerShell 调：exe 是 GUI 子系统，cmd 和
PowerShell 的 & 都不会等它，也拿不到退出码（$LASTEXITCODE 是 null）。
subprocess 会真正等待进程结束。
"""

import os
import subprocess
import sys
from pathlib import Path


def main(argv):
    exe = Path(argv[1] if len(argv) > 1 else "dist/SyncBuddy/SyncBuddy.exe").resolve()
    if not exe.is_file():
        print(f"✘ 找不到 exe：{exe}")
        return 1

    script = exe.parent / "_internal" / "wbsync" / "test_remap.py"
    if not script.is_file():
        print(f"✘ wbsync 没打进包里：{script}")
        return 1

    env = dict(os.environ, WB_SYNC_REPO=str(Path.cwd()))
    try:
        r = subprocess.run([str(exe), "--run", str(script)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=env, timeout=120)
    except subprocess.TimeoutExpired:
        print("✘ 执行超时（120s）")
        return 1

    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if out:
        tail = out.splitlines()[-1]
        print(f"  {tail}")
    if err:
        print(err, file=sys.stderr)

    if r.returncode != 0:
        print(f"✘ test_remap 失败，退出码 {r.returncode}")
        return r.returncode

    print(f"✔ 打包产物可用：{exe.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
