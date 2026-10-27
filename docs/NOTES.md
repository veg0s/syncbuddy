#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2026 Veg0s
#

# 开发笔记

踩过的坑，和当时为什么这么选。写给以后接手的人——大概率是几个月后的自己。

## PyInstaller

### 打包后子进程调不动脚本

**现象**：exe 能启动、托盘正常，但一条都同步不了。

**原因**：`core.py` 原来靠 `python wbsync/export.py` 起子进程，`self.python` 取自
`config.json`，指向用户机器上的 `pythonw.exe`。打包后那台机器上没有 python。

**解法**：exe 自己当解释器，加一个分发入口。

```python
# syncbuddy.py
if len(sys.argv) >= 3 and sys.argv[1] == "--run":
    runpy.run_path(script, run_name="__main__")
```

```python
# core.py
def _runner(self, name):
    script = _script(name)
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run", str(script)]
    return [self.python, str(script)]
```

`wbsync/*.py` 作为数据文件一起打包（明文），运行时在 `_internal/wbsync/` 下。

**注意相对路径**：`--run` 的参数如果是相对路径，必须按 exe 所在目录解析，不能交给
`runpy` 按 cwd 算。CI 在仓库根调用时，`_internal/wbsync/x.py` 会被找成
`<repo>/_internal/wbsync/x.py`，少一层目录。

### windowed 模式下 sys.stdout 不是 None

**现象**：脚本里的 `print("✔ ...")` 报 `UnicodeEncodeError: 'gbk' codec can't encode character '\u2714'`。

**原因**：一直以为 windowed exe 的 `sys.stdout` 是 `None`，其实 PyInstaller 6.x 会塞一个
GBK 编码的包装器。判断 `if sys.stdout is None` 换 devnull 的逻辑根本没触发。

**解法**：不管是不是 None，一律强制 UTF-8。

```python
for name in ("stdout", "stderr"):
    stream = getattr(sys, name, None)
    if stream is None:
        setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    else:
        stream.reconfigure(encoding="utf-8", errors="replace")
```

### `__file__` 指向 `_internal/`

**现象**：打包后日志写到 `_internal/logs/`，用户找不到；仓库定位也找错地方。

**解法**：`paths.py` 里把两者分开。

```python
HERE = Path(__file__).resolve().parent          # 源码在项目目录，打包在 _internal
APP_DIR = (Path(sys.executable).resolve().parent
           if getattr(sys, "frozen", False) else HERE)
```

日志、`state.json`、单实例锁全部用 `APP_DIR`。仓库定位先看 `APP_DIR` 里面，
再看 `APP_DIR.parent`，这样绿色版把 `wb-sync/` 放在 exe 旁边或者上一级都能找到。

### 选 onedir 不选 onefile

onefile 每次启动要把 100MB 解压到临时目录，PySide6 尤其慢，托盘程序每次开机等十几秒
不能接受。体积代价是解压后 121MB 对 50MB 的 zip，无所谓。

`excludes` 里排掉用不到的 Qt 模块（WebEngine、Quick、3D、Multimedia 一整套），
不然 PySide6 的 hook 会把 300MB 全塞进来。

**但 `sqlite3` 和 `email` 不能排**：前者要读 `workbuddy.db`，后者被 `urllib.request`
依赖来做 header 解析。这两个是踩出来的——第一次打包排了 `sqlite3`，
`ModuleNotFoundError` 直接指向 `import.py` 第 33 行。

## Qt / PySide6

### `self.done = False` 覆盖了 `QDialog.done()`

**现象**：点“完成”时弹 `Error calling Python override of QDialog::done(): 'bool' object is not callable`。

**原因**：`QDialog` 有内置方法 `done(int)`，我用同名属性存“用户是否点了完成”，
把它覆盖成布尔值。`self.accept()` 内部要调 `self.done(Accepted)`，于是变成 `False(1)`。

**教训**：Qt 的这些名字不能当属性名——`done / accept / reject / close / show / hide /
update / size / width / height / parent / layout / style / paint / event / key / focus`。
改成 `self.ok` 就好了。

### QMessageBox 在无头环境会永久阻塞

**现象**：自检跑一半没输出了，也不报错，进程挂着。

**原因**：`go_next()` 从第 2 页翻第 3 页时，age 私钥为空会弹警告框。这在真实使用中
是对的（提醒但不阻止），但在 offscreen 测试里 `exec()` 永远等不到人点。

**解法**：测试里先把条件补齐再翻页。凡是可能弹窗的路径，测试都要先喂好数据。

### 布局拥挤时 QLineEdit 被压扁

**现象**：内容变多后输入框高度掉到 24px，文字上下各被裁掉一半。

**原因**：`QWidget.addStretch()` 抢不到空间时，Qt 会按各控件的 minimumSizeHint 压缩，
`QLineEdit` 的 minimum 很小。

**解法**：需要稳定高度的控件显式 `setFixedHeight(32)`，页面本身套 `QScrollArea`。

## 路径与配置

### 密钥路径：不扫描磁盘，但要看自己的配置

这条边界绕了一圈才想清楚。

**错的**：解析 `~/.ssh/config` 找 `IdentityFile`，扫 `~/.config/age/*`。
程序去翻用户的私钥位置，是一种冒犯，而且经常翻错（有人用 MobaXterm、Pageant、
1Password agent，私钥根本不在 `~/.ssh` 下）。

**对的**：
- 首次运行、没配置过 → 输入框留空，让用户自己指定绝对路径
- 配置过 → 从 `config.json` 的 `hosts[本机主机名]` 段读回来显示

读自己上次存的值不是猜测，是回显。

### config.json 不只是本机设置

它是**多机共享的路径映射表**。`import.py` 靠它把 A 机器的绝对路径重写成 B 机器的，
所以不能因为“里面有个人路径”就从仓库里拿掉，那样跨设备导入会失效。

里面有主机名、用户名、私钥**路径**（不含密钥内容）。公开数据仓库前自己看一眼即可。

### 环境变量要在 importlib 之前设

**现象**：移动脚本后，`missing config.json: ...\wbsync\config.json`。

**原因**：`core.load_wb_config()` 用 `importlib.spec_from_file_location` 直接加载
`wbsync/import.py`。那个模块在 import 那一刻就求值 `ROOT = os.environ.get("WB_SYNC_REPO") or HERE`。
而 `WB_SYNC_REPO` 只在子进程调用时注入（`_sync_env()`），同进程的 importlib 加载时还没有。

**解法**：`core.py` 模块级、`WB_SYNC` 定义之后立刻：

```python
os.environ.setdefault("WB_SYNC_REPO", str(WB_SYNC))
```

用 `setdefault` 而不是直接赋值，外部还能覆盖。

### .gitignore 白名单制的坑

数据仓库的 `.gitignore` 是“默认排除一切，按需放行”：

```
*
!*/
!*.py        ← 这行放行了所有 .py，包括 data/skills 下 66 个技能脚本
!data/**
```

要排除根目录那四个工具脚本，规则必须写在**文件最后**（后写优先）：

```
/export.py
/import.py
/doctor.py
/test_remap.py
```

`/` 前缀只匹配根目录，不影响 `data/skills/**/*.py`。

## 持续集成

### GUI 子系统的 exe，shell 不会等它

**现象**：`test_remap` 明明 8 项全过，CI 判失败，`$LASTEXITCODE` 是 null。

**原因**：exe 编译成 GUI 子系统（`console=False`）。无论是 shell 的 `&` 还是绕一层
命令解释器，启动之后立即认为命令结束——不等待、也拿不到退出码。

**解法**：用 Python 的 `subprocess`，它会真正等待进程退出。见 `tools/verify_build.py`。

### Windows runner 默认 stdout 是 cp1252

**现象**：校验脚本打印中文和 `✔` 时 `UnicodeEncodeError: 'charmap' codec`。

**解法**两层：输出流 `reconfigure(encoding="utf-8")`；子进程输出里的 `✔`/`✘`
先换成 `[ok]`/`[x]` 再打印。纯 ASCII 到哪都不会炸。

本地复现：`PYTHONIOENCODING=cp1252 python tools/verify_build.py ...`。

### 清理构建目录的坑

PowerShell 的 `Remove-Item` 和 Python 的 `shutil.rmtree` 在某些环境里会被安全策略
拦掉（比如带了回收站代理）。拦掉之后 PyInstaller 复用旧产物，打出来的 exe 还是上一次的
——表现为“代码明明改了，行为没变”。

遇到这种情况用 `--distpath dist_new --workpath build_new --clean` 强制重建。

## 几个设计决定

**为什么用 git 当传输层**：会话正文是成千上万个 jsonl 小文件，天然适合 git 的增量。
冲突按 UUID 合并，不搞行级 diff。

**为什么记忆要 age 加密**：其余内容（会话、技能、配置）明文同步是有意的，方便排查；
但 `SOUL.md`、`MEMORY.md` 这类东西是私人的，端到端加密更合适。

**为什么 MPL 而不是 MIT**：改过的文件要回馈，但不拖累整个工程。可以嵌进闭源产品用，
只要改到的那几个文件保持 MPL。

**为什么代码和数据分开放**：`wb-sync/` 是用户的私有数据，不进这个仓库；工具通过
`WB_SYNC_REPO` 或者目录约定找到它。这样开源的是工具，不会连人的会话记录一起公开。

## 发布之后才暴露的问题

源码模式下测不出来，打包给别人用才发现的。记下来免得下次再犯。

### 主程序日志一个字都没写

`_setup_io()` 里原来是 `if sys.stdout is None: sys.stdout = stream`。
以为 windowed exe 的 stdout 是 None，其实是个 GBK 包装器，条件永远不成立。

结果是打包版跑起来日志干干净净——用户报"首次扫描很久，看着像卡住"，
我这边一条日志都拿不到，只能靠猜。

修法：不看它是不是 None，无条件把文件流接进去。用 `_Tee` 同时写控制台和文件：

```python
class _Tee:
    def __init__(self, *streams): self.streams = streams
    def write(self, s):
        for st in self.streams:
            try: st.write(s)
            except Exception: pass
```

顺带发现 `--run` 分支也有同样的假设，一起改了。

### 向导里填的远程地址存不下来

两个独立的错叠在一起：

1. `git remote set-url` 对**刚 git init 的仓库**会失败——那时没有 `origin`，
   得用 `remote add`。代码里只写了 set-url，报错又被 `capture_output=True` 吞了。
2. `write_text` 写 config 在前，处理 remote 在后。就算 set-url 成功，
   `config.json` 里也不会有 remote 字段。

表现就是：导完向导，设置里看不到远程仓库，重跑向导输入框还是空的。

修法：先写 `cfg["remote"]`，`set-url` 失败再 `add`，最后才落盘。

### 没有 age 公钥直接 KeyError

`export_memory()` 第一行 `pubkey = cfg["age_public_key"]`。
用户没配 age 密钥对（很多人只用会话同步），导出直接崩在 `KeyError` 上，
而且报的是脚本内部行号，看着像代码 bug 而不是配置缺失。

修法：`cfg.get("age_public_key")` 判空，没配就跳过这一层并打印一行说明。
一个可选功能的缺失，不该让整个同步流程挂掉。

### 一点体会

这轮三个 bug 有个共同点：都是**在源码模式下永远碰不到的分支**——
windowed 的 stdout、刚 init 的空仓库、没配的 optional 字段。

所以验证不能只在开发机上跑一遍源码。至少要做一次"干净环境"的端到端：
解压到空目录、按文档从头配一遍、看着日志走完全程。

### 裸 git push 推不上去

**现象**：日志里 `⚠ 上传失败，已标记待补传：fatal: The current branch main has no upstream branch.`

**原因**：`push()` 里写的是裸 `git push`。刚 `git init` 的仓库没有上游，
git 不知道往哪推。本机既有仓库早就设过 upstream，所以一直没暴露。

**解法**：`git push -u origin main`。`-u` 幂等，第一次顺便把 upstream 设好。

同时把 `fetch` 也改了：远程仓库一个 commit 都没有时 `fetch origin main` 会报
`couldn't find remote ref main`，这在首次使用是正常状态，不该当故障。
`git_state` 返回 `no_upstream` 时拉取直接跳过，别去 merge 一个不存在的远程分支。

### 合并无关历史被 git 拒绝

**现象**：`⚠ 上传异常：合并失败：fatal: refusing to merge unrelated histories`

**原因**：解压版第一次用时，本地 `wb-sync` 是现 `git init` 出来的；远程那边可能有
别的提交（建仓库时勾了 README，或者之前推过）。两个历史没有共同祖先，git 默认
拒绝合并。

**解法**：`merge_remote()` 加 `--allow-unrelated-histories`。配合原有的 `-X theirs`
（冲突取远程），合并完再重新 export 把本机数据补回去。

本地 bare 仓库复现验证过：不加参数报同样的错，加了之后 `Auto-merging` 正常完成。
