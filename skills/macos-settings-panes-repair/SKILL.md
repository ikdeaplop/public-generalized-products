---
name: macos-settings-panes-repair
description: 修复 macOS「系统设置」侧边栏项丢失/面板空白（只剩「通用」「聚焦」、隐私与安全性打不开、授权窗口是空的）。当用户说「系统设置里没有那些设置项了」「隐私与安全性不见了」「设置界面是空白的」「点授权链接打开是空的」「辅助功能列表不显示」时使用。也用于诊断 LaunchServices / PluginKit 扩展注册库损坏，以及用辅助功能 API（AX）自动化读取/操作系统设置界面。
agent_created: true
---

# 修复 macOS 系统设置面板丢失

## 原理（先理解再动手）

macOS Ventura 以后，「系统设置」的侧边栏**不是写死的列表**，而是由一堆独立的
ExtensionKit 扩展（`/System/Library/ExtensionKit/Extensions/*.appex`，一个面板一个）动态拼出来的。

启动流程：

```
系统设置.app
  └─ 向 LaunchServices/PluginKit 查询扩展点 com.apple.Settings.extension.ui
       └─ LaunchServices 数据库（/var/folders/*/*/C/com.apple.LaunchServices.dv）
            └─ 返回 .appex 清单 → 侧边栏渲染
```

**LaunchServices 里这些扩展记录丢失 → 侧边栏只剩内置的「通用」和「聚焦」**，
点 `x-apple.systempreferences:...Privacy_Accessibility` 这类深链时因为找不到面板，
就弹出一个**空窗口**（用户看到的现象）。

诱因常见于：长时间不重启、macOS 更新卡住半安装状态、清理工具误删缓存。

## 诊断（3 条命令确诊）

```bash
# 1. 设置扩展注册数（正常应有几十条，只剩 GeneralSettings/Spotlight 即异常）
pluginkit -m -A | grep -i "Settings.extension"

# 2. LaunchServices 里 ExtensionKit 扩展记录数（正常几百条，为 0 即确诊）
LSR="/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister"
"$LSR" -dump 2>/dev/null | grep -c "ExtensionKit/Extensions"

# 3. 磁盘上实际有多少扩展（对照组，通常 ~590 个）
ls /System/Library/ExtensionKit/Extensions/*.appex | wc -l

# 4. 也可直接看单个扩展能否被扫描（会报 -10811 是 kLSNotAnApplicationErr）
"$LSR" -f -v /System/Library/ExtensionKit/Extensions/SecurityPrivacyExtension.appex
```

注意：macOS 里 `log` 是 zsh 内建命令，查日志必须写全路径 `/usr/bin/log`。

## 修复

```bash
# 0. 退出系统设置
osascript -e 'tell application "System Settings" to quit' 2>/dev/null; pkill -f "System Settings"; sleep 2

# 1. 备份并移走可能损坏的配置/缓存
BK=/tmp/syspref_backup_$(date +%H%M%S); mkdir -p "$BK"
mv ~/Library/Preferences/com.apple.systempreferences.plist "$BK"/ 2>/dev/null
mv ~/Library/Caches/com.apple.systemsettings.menucache "$BK"/ 2>/dev/null

# 2. 重建 LaunchServices 数据库（system 域 3 秒；全量 1-5 分钟）
LSR="/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister"
"$LSR" -kill -r -domain system
"$LSR" -kill -r -domain local -domain system -domain user

# 3. 重启守护进程让其重新播种
killall lsd; sleep 3; killall pkd; sleep 3

# 4. 重开系统设置，验证侧边栏
open -a "System Settings"; sleep 10
```

验证：`pluginkit -m -A | grep -ci "Settings.extension"` 应从 2 升到 10+，
`"$LSR" -dump | grep -c "ExtensionKit/Extensions"` 应从 0 升到 ~400。

## 用 AX API 读/操作系统设置（自动化辅助）

前提：执行进程要有「辅助功能」权限（`AXIsProcessTrusted()` 返回 True）。
注意 **SwiftUI 的 AX 树存在自引用**（app 的 children 里又是 app），
所以 `entire contents` 或无限递归会爆栈，**必须限制深度**。

关键点：

- 取进程：`NSWorkspace.sharedWorkspace().runningApplications()` 里按
  `bundleIdentifier()=="com.apple.systempreferences"` 匹配（本地化名字是「系统设置」，
  用英文名匹配会失败）。
- 遍历：`AXUIElementCopyAttributeValue(el, "AXChildren", None)`（字符串形式；
  pyobjc 里 `kAXChildrenAttribute` 等常量不一定导出，用字符串最稳）。
- 读勾选框：先找 `AXStaticText` 文本 == 应用名，再取它的父链内的 `AXCheckBox`，
  读 `AXValue`（1=已勾选，0=未勾选）。
- 点勾选框：`AXUIElementPerformAction(checkbox, "AXPress")`。
  **但 macOS 会弹「输入密码允许此操作」，必须用户本人验证（Touch ID / 密码），
  程序无法代替。** 这是硬限制，提前告诉用户，不要反复尝试。
- 打开指定面板：
  `open "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility"`
  （旧写法 `com.apple.preference.security` 在新系统上会落到空窗口）
- 面板内容可能异步加载较慢，读到空树时 `sleep 10~20` 再试，别急着下结论。

## 授权类排查：程序改权限不生效

`tccutil reset Accessibility <bundle-id>` **不需要密码**即可清掉旧记录，
然后启动目标 App 让它自动重新登记，再由用户勾选 + 验证。
这是「勾了但没生效（二进制换过 / 路径变过）」最可靠的复位路径。

诊断顺序：

```bash
tccutil reset Accessibility com.example.app        # 清旧记录
open -a "/Applications/示例.app"                    # 启动以重新登记
sleep 6                                             # 列表里会出现该 App（未勾选）
# 再请用户勾选并验证
```

## 常见坑

- `~/Library/Group Containers/com.apple.systempreferences.cache` 和
  `/var/folders/*/*/C/com.apple.LaunchServices.dv` **受 TCC/SIP 保护**，
  没有「完全磁盘访问权限」时 `mv`/`rm` 都报 `Operation not permitted`，
  不要在这上面浪费时间——重建（步骤 2/3）就够了。
- 用 Bash 工具跑 `log` 会报 `too many arguments`，用 `/usr/bin/log`。
- 改系统设置面板后系统设置可能被自动终止（automatic termination），
  每次操作前重新 `open` 一次。
- 屏幕截图 `screencapture` 需要「屏幕录制」权限，没授权时**只会截到壁纸**，
  看到「没有窗口」不要误判成界面空白——用 AX 树判断才准。
