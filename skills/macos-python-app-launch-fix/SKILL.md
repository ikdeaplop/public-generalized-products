---
name: macos-python-app-launch-fix
description: 排查并修复 macOS 上 Python 打包的 .app「双击没反应 / 打不开 / 闪退」。当用户说「点了打不开」「双击没反应」「app 闪退」，或 Python 应用在本地能跑但打包成 .app 后双击失败时使用。也适用于给脚本型 .app 增加可观测启动日志、ad-hoc 签名、第三方依赖可选化。
agent_created: true
---

# macOS Python .app 双击打不开排查

## 核心原则

「双击没反应」= 进程起不来或起来立刻退出，**且错误被 LaunchServices 吞掉**。
第一步永远是**让错误可见**，不要靠猜。

## 第一步：把 launcher 改成可观测版（最关键）

脚本型 .app（CFBundleExecutable 指向 bash 脚本）默认吞掉所有输出。改 `Contents/MacOS/launcher`：

```bash
#!/bin/bash
LOGDIR="{项目目录}/data"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/launch.log"
echo "==========================================" >> "$LOG" 2>&1
echo "启动时间: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG" 2>&1
echo "用户: $(whoami)" >> "$LOG" 2>&1
cd "{项目目录}"
"{python 绝对路径}" "{项目目录}/main.py" "$@" >> "$LOG" 2>&1
CODE=$?
echo "退出码: $CODE" >> "$LOG" 2>&1
if [ "$CODE" -ne 0 ] && [ "$CODE" -ne 143 ]; then
    osascript -e "display dialog \"启动失败（退出码 $CODE）。\n\n详情日志：\n$LOG\" buttons {\"好\"} default button 1 with title \"App\" with icon caution" >/dev/null 2>&1 &
fi
exit $CODE
```

要点：
- **不要用 `exec`**，否则拿不到退出码、也无法弹窗
- 退出码 143（SIGTERM）不算失败，正常关闭会触发
- 弹窗用 `&` 放后台，避免阻塞脚本退出

## 第二步：复现（在自动化环境里不能用 `open`）

Agent/沙箱环境下 `open xxx.app` 通常**静默无效**（系统日志里连 launcher 痕迹都没有）。
用这个模拟真实双击：

```bash
launchctl asuser $(id -u) /usr/bin/open "${HOME}/Desktop/App.app"
sleep 10
ps aux | grep -i "main.py" | grep -v grep
cat 项目目录/data/launch.log
```

## 高频根因（按命中率排序）

### 1. 顶层 import 第三方库在 launchd 环境崩溃
**最隐蔽**：终端 `python main.py` 正常，双击必崩。因为 launchd 拉起的进程环境变量与终端不同。

典型案例：`import pyautogui` → `mouseinfo` → `rubicon-objc` →
`AttributeError: dlsym(0x..., objc_msgSendSuper_stret): symbol not found`（macOS 15 上该符号已移除）。

修法：把非核心依赖改为**可选化**，不要让一个兜底功能拖垮整个 App：

```python
try:
    import pyautogui
    pyautogui.FAILSAFE = True
    PYAUTOGUI_OK = True
    PYAUTOGUI_ERR = ""
except Exception as _e:
    pyautogui = None
    PYAUTOGUI_OK = False
    PYAUTOGUI_ERR = str(_e)
```

然后：
- 所有用到它的入口统一走一个守卫方法（缺失时弹中文提示 + 引导替代方案）
- 启动时若检测到不可用，自动切到不依赖它的主模式
- **注意**：模块级的 `pyautogui.FAILSAFE = True` 之类赋值要一起挪进 try，否则 None 上赋值照样崩

### 2. 漏 import（静态检查能抓到）
`grep -n "某函数" main.py` 若只在调用处出现、没有 import 语句 → 调用时才 NameError。
用 Grep 工具交叉验证，别只看 Bash grep。

### 3. 未签名 bundle 被拦截
```bash
codesign --force --sign - --timestamp=none "App.app"   # 正确
codesign --force --sign - --deep "App.app"             # 错误！脚本型 bundle 会 sealed resource 校验失败
codesign -v "App.app"                                   # 校验
xattr -l "App.app"                                      # 查 quarantine
spctl -a -vv "App.app"                                  # Gatekeeper 评估
```

### 4. 依赖的可执行文件没装（Playwright 典型）
`pip install playwright` 只装 Python 包，**浏览器二进制要单独下载**：
```bash
python -m playwright install chromium      # 约 95MB，装到 ~/Library/Caches/ms-playwright/
ls ~/Library/Caches/ms-playwright/         # 确认 chromium-xxxx 与 chromium_headless_shell-xxxx 都在
```
有头模式（headless=False）需要完整 chromium，不只是 headless shell。

## 打包脚本检查清单

- `Info.plist` 用 `plutil -lint` 校验
- launcher `chmod 0o755`，`CFBundleExecutable` 与文件名一致
- 版本号在 Info.plist（`CFBundleVersion` / `CFBundleShortVersionString`）和 app 名两处同步
- 先签名，再 `touch` bundle 刷新 Finder 图标
- 旧版本移入 `~/.Trash`，用 `shutil.move`，不要 `rm -rf`

### 5. 功能"总是失败"但代码看着没问题 → 实测抓真实数据
不要靠猜页面结构或接口，直接用 Playwright 打开目标页打印实况：

```python
p.goto(url); p.wait_for_timeout(2500)
print(p.url, p.title())
for t in ("登录", "扫码登录", ...): print(t, len(p.get_by_text(t, exact=False).all()))
print(e.ctx.cookies())   # 关键：看真实 cookie
```

**真实案例（闲鱼扫码登录必失败）**：
- 死循环：登录判据读本地 cookies 文件，而该文件只有登录成功才写入 → 永远检测不到
- 判据错：以为 `cookie2` / `_m_h5_tk` 是登录态，实测**游客态就有** → 未登录误判为已登录
- 修法：判据收紧为只有登录后才写的 cookie（`unb`/`lgc`/`sgcookie`/`tracknick` 等），
  并在登录过程中**实时读浏览器 context 的 cookie**，而不是读落盘文件

## 交付前验证

1. `python -m py_compile main.py`
2. 离屏跑一次：`QT_QPA_PLATFORM=offscreen python main.py`（后台启动 + sleep + kill），确认无 traceback
3. `launchctl asuser` 模拟双击，确认进程常驻且应用日志记录到启动
4. 关键依赖各做一次可用性断言（如浏览器引擎能否真的打开页面）
