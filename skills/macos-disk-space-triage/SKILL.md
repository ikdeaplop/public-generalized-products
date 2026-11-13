---
name: macos-disk-space-triage
description: 排查并清理 macOS「系统资源不够用 / 磁盘满 / 空间不足 / 电脑变卡」。当用户说「资源不够用」「磁盘满了」「空间不够」「电脑卡」「存储空间不足」，或需要定位是什么在吃 CPU/内存/磁盘并安全清理时使用。含 APFS purgeable 与本地快照导致「删了不释放」的坑、Chrome 失控进程处置、按层钻取 du 的方法。
agent_created: true
tags: [macos, disk, cleanup, apfs, snapshot, performance, triage]
---

# macOS 磁盘/资源三分类排查与安全清理

> 2026-10-01 在 M4 Mac mini 枢纽机实测沉淀。用户报「系统资源不够用」，
> 真凶是 **磁盘 96% 满** + **Chrome 失控进程**，与本机 AI 任务无关。

## 第 0 步：先分类，别急着清（关键）

「资源不够用」可能指三件完全不同的事，**先测再动**：

```bash
uptime                                              # 负载（M 系列核数多，>核数 才算真高）
ps -Ao %cpu,rss,comm -r | head -12                  # CPU/内存大户
memory_pressure | head -8                           # 内存压力（看 Pages free / purgeable）
df -h /System/Volumes/Data                          # 真实空间（一定要看 Data 卷）
diskutil info /System/Volumes/Data | grep -i "Container Free"   # 容器可用（比 df 准）
diskutil apfs list | grep -E "Capacity|Volume|Consumed"          # 各卷消耗
tmutil listlocalsnapshots /                         # 本地快照（吃盘隐形大户）
```

**经验**：CPU 高优先看 Chrome；磁盘满看 Data 卷；两者常同时出现但成因无关。

## 第 1 步：CPU 失控进程（常见 = Chrome）

```bash
ps -Ao pid,ppid,%cpu,rss,command | grep "[G]oogle Chrome" | sort -k3 -rn | head -8
```
识别 `--type=renderer` / `--type=gpu-process` / 无 type（MAIN browser）。

**处置**：只杀异常子进程，别杀浏览器本体：
```bash
kill -TERM <renderer_pid> <gpu_pid>     # 主进程通常随后自己降下来
```
实测：主进程 96% + renderer 58% + gpu 36% → 杀子进程后 **合计 191% 降到 0%**。

## 第 2 步：磁盘占用逐层钻取（只读，先扫后删）

```bash
du -sh ~                                    # home 总量
du -d 1 -h ~ | sort -rh | head -15           # home 一级
du -d 1 -h ~/Library | sort -rh | head -15   # Library 一级
du -d 1 -h ~/Library/Containers | sort -rh | head -10
du -d 1 -h "$HOME/Library/Application Support" | sort -rh | head -12
du -d 1 -h /Applications | sort -rh | head -25
find / -xdev -type f -size +1G 2>/dev/null | head -60   # 大文件（找出 VM/镜像/模型）
```
> `du -d 1` 与 `-s` 不能混用（`du -d 1 -sh` 会静默返回空）——踩过这坑。

## 核心坑：删了却不释放（APFS）

删掉 9.7G，`df` 只涨 2.5G。两个原因：
1. **purgeable**：macOS 本就把可清除数据算进"可用"，删它不改变 `df` 值。
2. **本地快照钉住**：`com.apple.os.update-*` 等快照仍引用旧数据 → 不回收。
```bash
tmutil listlocalsnapshots /                        # 有快照就是嫌疑
tmutil deletelocalsnapshots <volume|date>          # 系统级动作，必须用户确认后再删
```
**验收判据**：`Container Free` 数值 + 磁盘上真实 `du`，**不是 `df` 单值**。

## 第 3 步：安全清理顺序（可再生 → 需确认）

**A. 可再生（直接删，非个人文档，随时重建）**
```bash
~/homebrew/bin/go clean -modcache            # Go 模块缓存
~/.local/bin/pnpm store prune                # 磁盘将满时极慢（实测 >14min，可跳过）
rm -rf ~/.cache/*                            # XDG 缓存
rm -rf ~/Library/Caches/*                    # App 缓存（会重建）
find ~/.workbuddy/traces -maxdepth 1 -type d -mtime +7 -exec rm -rf {} +
```
**B. 需确认（个人数据）** → 先出清单，再逐项确认：
- `~/Library/Containers/com.tencent.xinWeChat/Data/Documents`（微信聊天文件，**走微信内置存储管理**，勿手删）
- `~/.archive/*`（用户归档：剪映草稿、历史等 → 建议迁外置盘/网盘）
- `/Applications`（按「大小 + 安装日期 + 是否曾运行」出候选清单让用户勾）
- 废纸篓 `~/.Trash`（清空才释放）

**C. 移到废纸篓（可反悔，但同一卷不释放空间）**
```bash
trash <path>...        # macOS 26 内置 /usr/bin/trash；验证：先 trash 个临时文件看 ~/.Trash
```

## 判断「曾运行过」的替代信号（Spotlight 挂时）

`mdls -name kMDItemLastUsedDate` 常返回 null。改用存在性判断：
`~/Library/Preferences/<bundleid>.plist` / `Containers/<bid>` / `Saved Application State/<bid>.savedState`。
再配合 `.app` 包 mtime（约等于安装/更新日期）。

> 🔴 **无运行痕迹 ≠ 可删**（2026-10-01 实测教训）：这条启发式会**误伤自建工具**——
> 自己写的小工具常常没有偏好文件/容器目录。
> **必须人工复核一遍，把「自建/自制」的一律剔除**（识别信号：`scg*`、`本地语音输入`、
> `ClashVerge整理器`、`Codex++` 这类名字；或 bundleid 是自己拼的、`CFBundleShortVersionString` 为空）。
> 这类删了无源可复原，而它们通常很小（本次 5 个才 0.4G），**不值得为几个 G 冒险**。
> 反之，可公开重装的商业 App 才是安全目标。

## 🔴 快照：先判别「活快照」，再谈删

```bash
sudo tmutil deletelocalsnapshots /      # 常失败：
#   Failed to delete all Time Machine local snapshots ... status 70 (EX_SOFTWARE)
```

**为什么删不掉**：macOS 的 `/` 是**密封快照**。`diskutil apfs list` 里 disk3s1(System) 的
`Snapshot: <uuid>` + `Snapshot Disk: disk3s1s1` + `Snapshot Mount Point: /` + `Sealed: Yes`
—— **那一个就是正在运行的根快照**。`tmutil` 只认 `com.apple.TimeMachine.*` 本地快照，
对这些 `com.apple.os.update-*` 列得出、删不掉。

```bash
sudo diskutil apfs listSnapshots disk3s1     # 列本卷快照
diskutil apfs list | grep -A4 "Mount Point:      /"   # 找活的那个
mount | grep "System/Volumes/Update"         # 有挂载=有更新暂存，更别动
```

**结论**：看到 `Mount Point: /` + `Sealed: Yes` 就**收手**；
改用 `diskutil apfs deleteSnapshot` 硬删 = **系统起不来**，且可能损坏待完成的系统更新。
这类快照 macOS 自己会回收。**别把「删快照」当腾空间手段。**
真 TM 快照长这样 `com.apple.TimeMachine.2026-10-01-123456.local`，那才可以删。
详见坑件 `2026-10-01-macOS根分区密封快照不可删-tmutil删快照失败.md`。

## 安全红线

- **NEVER** `rm -rf` 桌面/下载/文档/家目录；**NEVER** 删 `~/your-vault`（知识库）。
- 微信 `Documents` 里是聊天记录，只清 Cache/tmp。
- 删本地快照、清空废纸篓 = 不可逆，**必须用户明确同意**。
- 分批 <50 项/次，避免批量删除审批风暴。

## 追加实测：「清缓存涨不动数字」是正常的（2026-10-01 复测）

清掉 ≈12G 可再生缓存（go / Caches / .gradle/caches / .npm/_cacache / .bun/install/cache / logs / traces），
`df` 与 `Container Free` 几乎不动（恒 ~10.9G）。**这不是没清掉，是 APFS `purgeable`**：
macOS 本来就把这些算进"可用"，删它不改变可用值。**结论：缓存级清理有天花板，到此为止。**
再要实质释放只能动"非 purgeable 的实体数据"——且每一样都需要用户拍板或 sudo：

| 目标 | 量级 | 需要什么 |
|---|---|---|
| 废纸篓 `~/.Trash` | 9.7G | 用户确认（不可逆）。构成可用 `ls ~/.Trash \| grep -vcE "^[0-9a-f]{128}$"` 区分"机器产物 vs 人类删除物" |
| `com.apple.os.update-*` 本地快照 | ≈7G | **sudo**（`tmutil deletelocalsnapshots`），脚本内跑不了 |
| `~/.archive` | 27.9G | 用户选择迁外置盘/网盘 |
| 微信容器 | 24G | 走微信内置存储管理，勿手删 |
| `/Applications` | 34G | 用户勾选卸载 |

### 废纸篓里的 128 位 hash 命名条目是什么
`~/media/automation-archive/archive_dedup.py` 去重时把重复项按内容 hash 命名后丢进废纸篓——
所以看到上万个 `[0-9a-f]{128}` 名字的条目**是你自己的去重流水线产物，不是用户手删的文件**。
判断归属：`du -sh ~/.Trash/* | sort -rh | head`，再看是否有真实文件名的目录。

### 磁盘满的静默失效该怎么查
```bash
grep -rn -i "no space left\|ENOSPC" ~/.workbuddy/logs/*.log
```
实测命中 `obsidian-kb.err.log`: `OSError: [Errno 28] No space left on device` ——
索引写入中断、进程退出、**不报警**。任何"任务莫名不产出"先查这一条。

## 卸应用与清空废纸篓的实操

```bash
# ① 卸应用：走 trash（可反悔），分批 ≤10
trash "/Applications/Lark.app" "/Applications/美图秀秀.app" ...
#    App Store 保护的应用会报 afpAccessDenied / Code=513（撤不掉）→ 提权：
sudo -n mv "/Applications/醒图.app" "$HOME/.Trash/醒图.app"
sudo -n chown -R "$(id -u):$(id -g)" "$HOME/.Trash/醒图.app"

# ② 清空废纸篓：用 Finder 原生（等价右键「清倒废纸篓」），别用 rm -rf
osascript -e 'tell application "Finder" to empty trash'
du -sh ~/.Trash && ls ~/.Trash | wc -l     # 复核应归 0
```

- 移动进废纸篓**同卷不释放空间**，必须清空才释放。顺序：先移应用 → 再清空 → 一次到位。
- 清空前先看清构成：`ls ~/.Trash | grep -cE "^[0-9a-f]{126}$"` 数去重流水线产物，
  `ls ~/.Trash | grep -vE "^[0-9a-f]{126}$"` 看真实条目。**注意本机去重产物是 126 位 hash，不是 128 位。**

## 本次实测结果（M4 枢纽，2026-10-01）

| 动作 | 释放 |
|---|---|
| Chrome 失控进程 | CPU 191% -> 0% |
| go / logs / traces / Caches / .cache | 约 9.7G |
| 归档垃圾 -> 废纸篓 | 1.5G（待清空） |
| 磁盘可用 | 7.9G -> 10G（容器 8.5 -> 11.0G） |
| 废纸篓清空 / 卸 11 个未用 App | **11G → 28G（95%→86%）**，Container Free 11.8→30.3G，净 **≈18.5G** |
