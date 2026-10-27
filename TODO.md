# SyncBuddy 待办

## 进行中

- [ ] 打包成绿色版 exe（PyInstaller）

## 已修复

- [x] **状态卡在「正在检查远程」**：`status` 只在「拉取并导入成功」那条分支才设 `synced`，
      仓库本来就一致时函数提前返回，状态永远停在进行中。
      改成 `sync_in` / `sync_out` 外层包一层 `_settle()`，不管走哪条分支都收敛到终态。
- [x] **同步卡死兜底**：`status` 变更时记 `status_ts`，超过 5 分钟仍停留在进行中就标为超时错误。
- [x] **错误信息被右边裁掉**：detail 加 `setWordWrap(True)`，长错误换行显示。
- [x] **长标题溢出**：标题用 `QFontMetrics.elidedText` 加省略号。
- [x] **图标垂直错位**：错误信息换行后行变高，图标被居中到文字块中间；改成 `AlignTop` 与标题对齐。
- [x] **不断弹出 cmd 窗口**：pythonw 无控制台，每个 console 子进程都会自己开窗；
      `watch.py` 每 2 秒调一次 tasklist，于是黑框不停蹦。
      统一走 `_proc.py`（`CREATE_NO_WINDOW`），该状态会被孙进程继承（git→ssh 也不弹）。
- [x] **多实例**：双击两次 = 两套 2 秒轮询，弹窗频率翻倍。加 `QLockFile` 单实例锁，冲突静默退出。
- [x] **pythonw 下 print 崩溃**：`sys.stdout` 为 None，`print` 抛 AttributeError 把程序带走。
      启动第一件事把 stdout/stderr 接到 `logs/runtime.log`；`core.log` 的 print 也加了保护。
- [x] **config 缺 exe 字段**：wizard 探测了却没写进 hosts 段，启动器找不到 WorkBuddy。

## 已完成

- [x] **预扫描** `export.py --plan`：只比对不写盘，输出按类别分组的待上传清单
- [x] **按类别过滤**：`--only` 贯穿导出与导入
- [x] **面板两页签**：待上传 / 待拉取，类别可折叠、可勾选
- [x] **同步历史窗口**（右键菜单），带清空
- [x] **设置里管类别自动同步**：关掉的类别既不推也不拉
- [x] 日志入口移到设置页
- [x] 面板顶部扫把图标（QPainter 画的，emoji 不渲染）
- [x] **托盘活动面板**（OneDrive 风格）：状态图标 + 最近同步记录 + 底部动作
- [x] 托盘图标随状态变色变符号（↔ 就绪 / … 检查 / ↑ 上传 / ✓ 完成 / ! 失败）
- [x] 同步事件记录（state.json 保留最近 30 条，面板展示 12 条）
- [x] 四态判断、sync_in / sync_out、补传、push 重试
- [x] 差异预览（标题 + 开场 120 字，含引用语法剥离）
- [x] 预览可勾选，取消勾选的不写进本机库（`import.py --exclude`）
- [x] 合并式导出（index 按 id 并集、transcripts 增量）
- [x] index.jsonl 读端去重
- [x] `watch.py` 进程监听（边沿 + 防抖，7 个用例全过）
- [x] HTTP(S) 代理支持（`core.py proxy-test` 可自检）
  - HTTPS 远程走 `http.proxy`
  - SSH 远程走 `connect.exe` 隧道，**路径必须用 8.3 短名**（含空格即使加引号也会被拆断）
  - 实测：直连 HTTPS 超时 8.6s，代理 409ms
- [x] `wizard.py` 首次配置向导（8 项自动探测，只需确认远程地址）
- [x] PySide6 界面：托盘、启动窗、预览窗、设置窗
- [x] `SyncBuddy.cmd` / `SyncBuddy-debug.cmd` 双击入口
- [x] `_selftest_gui.py` 离屏窗口自检

## 已知限制

- 从任务栏/开始菜单直接启动 WorkBuddy 时，SyncBuddy 来不及做启动前同步（只能捕获退出事件上传）
- 两台机器同时操作同一会话可能分叉，靠 merge -X theirs 保证不丢数据

