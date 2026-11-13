---
name: macos-input-hijack-hunt
description: 排查并止血 macOS 上「鼠标自己动 / 键盘自动输入 / 打不出字」的问题。当用户说「键鼠被控制了」「鼠标乱动」「键盘一直在输入某个字符」「打不出别的字」时使用。用 CGEventTap 抓出真正注入输入事件的来源 PID，二分排除嫌疑进程，最后用过滤型 EventTap 止血。
agent_created: true
---

# macOS 键鼠劫持排查与止血

用户说键鼠"自己动"时，**不要猜**。用系统级事件监听拿到硬证据，再二分排除，最后止血。

## 核心原理（先看这段，能省一半时间）

macOS 每个输入事件都带着**来源进程 PID**：

```python
pid = CGEventGetIntegerValueField(event, 0x3B)  # kCGEventSourceUnixProcessID
kc  = CGEventGetIntegerValueField(event, 9)     # kCGKeyboardEventKeycode
```

判读规则：
- **pid 有效且 `ps` 能查到** → 是这个进程在注入，直接处理它。
- **pid 有效但 `ps`/`sudo ps` 都查不到** → 来源在**驱动/HID 层**（虚拟 HID 设备、或键盘卡键）。
- **只有 KeyDown 没有 KeyUp + 固定间隔（macOS 默认约 83ms ≈ 12Hz）** → 系统按键重复，即某个键卡在"按下"状态。
- **pid = 0** → 真实硬件事件（用户在按键盘）。

## 排查流程

### 第 1 步：抓事件（决定性证据）

```bash
$P scripts/kb-monitor.py      # 监听 8 秒，打印每个事件的来源 PID + keycode
```

`$P` = `${PYTHON}`（**只有这个 venv 装了 pyobjc/Quartz**，系统 python3 和 3.13 都没有）。

需要「辅助功能」权限；若 tap 创建失败会明确提示。

### 第 2 步：排除远程控制

```bash
lsof -nP -iTCP -sTCP:ESTABLISHED | grep -iE "todesk|rustdesk|sunlogin|teamviewer|anydesk"
ps aux | grep -iE "screensharing|AppleVNCServer|ARD"
lsof -nP -iTCP -sTCP:LISTEN | grep -E ":5900|:3283|:22"
who
```
**零 ESTABLISHED 连接 = 没人远程操控**，别再往这个方向查。

### 第 3 步：列出有能力劫持输入的常驻

```bash
launchctl list | grep -viE "com.apple"          # 重点看 com.scg.* 自建任务
ps aux | grep -iE "osascript|cliclick|pyautogui"
grep -rlnE "keystroke|key code|CGEventPost|pyautogui|cliclick" ~/.workbuddy/bin/
```

### 第 4 步：二分排除

逐个停掉嫌疑常驻，**每次停完等 12~15 秒看拦截计数是否停止增长**（这一步必须有止血器在跑，否则用户一直在被喂字符）：

```bash
launchctl bootout gui/$(id -u)/<label>
# 顽固进程（launchd 会拉起）：
sudo pkill -f "<进程名>"; killall -9 <App名>
```

注意：输入法、Mac Mouse Fix 这类被 kill 后会被自动拉起，需结合"停掉后注入是否中断"来判断，而不是看进程是否消失。

### 第 5 步：判断是不是硬件

```bash
ioreg -c IOHIDDevice | grep '"Product" = ' | sort -u
```

若列出**多个键盘**（如同时有 USB 机械键盘 + 蓝牙键盘），且前 4 步都排除干净 → **大概率是其中某个键物理卡住**。让用户拔掉 USB 键盘 / 蓝牙键盘关机重连来确认。

## 止血：过滤型 EventTap

`CGEventTapCreate` 用 `kCGEventTapOptionDefault`（不是 ListenOnly），回调**返回 `None` 即丢弃该事件**。

```bash
# 按来源 PID 精准拦截（推荐，用户自己的键盘不受影响）
python scripts/key-blocker.py 50          # 按 keycode 拦截
# 自制精准版：只吞 pid==BAD_PID 的所有按键
```

启动后每 10 秒打印拦截计数；`pkill -f key-blocker.py` 停止。

**不要试图用 `CGEventPost` 发 keyUp 解除 stuck key** —— 实测无效，HID 栈的按住状态不认用户态合成的 keyup，只能拔键盘或重启。

## 踩过的坑

1. **别只看进程 CPU**：注入是每秒 12 次的轻量 post，进程 CPU 可能只有 0.1%，`ps aux -r` 完全看不出来。
2. **别用 subprocess 在回调里查进程名**：短命进程早退出了，只能拿到空。用 `ctypes` 调 `proc_pidpath` 同步取（快得多），但内核层来源照样拿不到。
3. **停嫌疑服务前先起止血器**：否则诊断期间用户一直在被灌字符。
4. **macOS 按键重复率约 83ms 一次**，看到这个间隔 + 只有 keydown，就是 stuck key，不是脚本循环。
5. 常见 keycode：50=Grave(`)、47=句点(.)、36=回车、49=空格、51=删除。中文输入法下 Grave 会输出「·」间隔号 —— 用户描述的"一直输入点"很可能是这个，别误判成英文句点。
