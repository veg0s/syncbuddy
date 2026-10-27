# ASAR 探测工具

WorkBuddy 主程序（`app.asar`）的只读探测工具。用来确认某项能力在客户端里是否真实存在、当前处于什么状态，而不是靠界面上有没有开关来猜。

## 为什么要有它

2026-10-26 排查「跨设备同步」时，界面上找不到任何同步开关，但反编译后发现：

- 云端长期记忆有一整套接口（`/api/memory/global/*`），确实跨设备
- 任务列表走 `/im/api/buddy/tasks` 中转，但会话正文的 endpoint 是本机 sidecar
- 官方另有 `ExpertCloudSyncService` 双向同步引擎，只管专家包和企业技能，不管个人 skills

这类结论只能从客户端里读出来。工具留着，版本升级后重跑一次就知道官方有没有变。

## 用法

```bat
cd /d D:\your\path\to\syncbuddy\tools

python asar_probe.py check                    云能力位体检（最常用）
python asar_probe.py paths memory             列出所有接口路径，可过滤
python asar_probe.py ls "memory.*\.js$"       找模块文件
python asar_probe.py show "main/server.js" 4000   打印某个文件的前 N 字符
python asar_probe.py grep "derivePresence" ".js$" 80 5
                                              正则检索：模式 文件过滤 上下文 上限
```

**asar 位置**：默认按 `WB_ASAR` 环境变量 → `%LOCALAPPDATA%\Programs\WorkBuddy\resources\app.asar`
→ `Program Files` 下的安装位置依次查找。装在别处时：

```bat
set WB_ASAR=D:\Portable\WorkBuddy\resources\app.asar
python asar_probe.py check
rem 或者
python asar_probe.py --asar "D:\Portable\WorkBuddy\resources\app.asar" check
```

加 `--all` 会扫描 30MB 以上的 bundle（默认跳过，多为第三方依赖，很慢）。

## 当前体检结论（2026-10-26，v5.9.2）

| 能力 | 状态 | 说明 |
|---|---|---|
| 云端长期记忆 API | 有 | 画像上云，跨设备一致；不含本地 SOUL/MEMORY 文件 |
| 任务列表 IM 中转 | 有 | 元数据中转，依赖在线主机 |
| 会话正文回放 | 有 | endpoint 取自本机 sidecar，PC 离线即失效 |
| 会话导入云空间 | 有 | 手动、单向、只读 |
| 专家/企业技能云同步 | 有 | 作用域不含个人 skills |
| 个人技能云同步 | **无** | 若变「有」，应停用自建同步方案 |

产品开关：`CloudAgentsSmartMerge=true`、`ScheduledTasks=true`、`SkillManage=false`、`DisableMemoryPersonalization=true`。

## 什么时候该重跑

- WorkBuddy 大版本升级后
- 官方更新日志提到"同步""多设备""任务列表"时
- 自建同步脚本因目录结构变化报错时（先 check 再看是不是官方改了 schema）

## 注意

- 纯只读，不改 asar、不改本地数据。
- asar 头部里 `offset` / `size` 有的是字符串，代码里已做 `int()`；unpacked 的条目没有 `offset`，访问前必须跳过。这两点踩过坑。
