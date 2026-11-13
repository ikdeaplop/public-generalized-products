---
name: macos-network-drop-triage
description: 排查 macOS 上「经常断网、网络时好时坏、手机端看不到这台电脑（WorkBuddy/远程控制/局域网设备显示离线）」的问题。定位隧道类工具（Cloudflare WARP / Clash TUN / Tailscale / 各类 VPN）劫持默认路由与 DNS 的病因，并用网关 vs 外网对比测试区分「无线问题」和「出口问题」。当用户说「经常断线」「老是离线」「网络不稳定」「手机上看不到我的电脑」「网络一会儿好一会儿断」时使用。
agent_created: true
---

# macOS 断网 / 离线排查

## 核心判断原则

**先分清是「无线链路问题」还是「出口链路问题」——两者修法完全不同，别一上来就折腾 Wi-Fi。**

判定方法就是两条 ping 的对比：

| 现象 | 结论 |
|---|---|
| 网关丢包 / 高延迟 | **无线链路问题** → 查信道干扰、信号、网线 |
| 网关 0 丢包且 <5ms，但外网丢包/高延迟 | **出口问题** → 查隧道/VPN/DNS 劫持（见下） |

## 排查顺序（照抄执行）

```bash
# 1. 有几个默认路由？（最关键的一步）
netstat -rn -f inet | grep "^default"
#    ✅ 只应有 1 条，指向 en0/en1
#    ❌ 出现第 2 条走 utun* → 有隧道工具在劫持全网流量

# 2. DNS 被谁接管了？
scutil --dns | grep -m3 nameserver
#    ✅ 192.168.x.x（路由器）或 223.5.5.5 / 119.29.29.29
#    ❌ 127.0.2.2 / 127.0.2.3   → Cloudflare WARP
#    ❌ 100.100.100.100         → Tailscale
#    ❌ 127.0.0.1 / 127.0.0.53  → 本地代理（Clash TUN 等）

# 3. 当前在用哪个接口？
ifconfig -a | grep -B1 "status: active"
route -n get default | grep -E "interface|gateway"

# 4. 网关 vs 外网对比测试（各 15–30 秒）
ping -c 30 -i 0.5 192.168.x.1     # 网关
ping -c 30 -i 0.5 223.5.5.5       # 国内 DNS，正常应 <15ms

# 5. 谁在跑隧道/代理？
ps aux | grep -iE "warp|cloudflare|tailscale|clash|mihomo|sing-box|v2ray|xray|openvpn|wireguard" | grep -v grep
```

## 各工具的特征与处置

### Cloudflare WARP（最常见的元凶）

特征：
- DNS = `127.0.2.2` / `127.0.2.3`（本地 DNS 代理）
- 多一条 `default link#NN UCSIg utun*`
- 网卡被伪造 IPv6，形如 `2606:4700:110:...`
- 国内目标延迟劣化到 200–400ms，丢包 10–40%

```bash
# 看状态（注意：直接调 App 内的二进制可能挂住，用 /usr/local/bin/warp-cli）
warp-cli status          # Connected + Warp 模式 + MASQUE = 在替所有流量兜圈子
warp-cli settings        # 看 Always On / Mode / exclude 列表
warp-cli disconnect      # 先断开验证病因（路由 DNS 应立刻恢复）
```

**彻底卸载**（官方自带脚本，逻辑干净，只清自己的东西）：

```bash
sudo "/Applications/Cloudflare WARP.app/Contents/Resources/uninstall.sh" -f
# 它会：杀 GUI → 删注册 → unload+删 daemon/updater 两个 plist
#       → 删钥匙串密钥 → 删 App → 删 /Library/Application Support/Cloudflare 与 ~/Library/Logs/Cloudflare
sudo rm -f /usr/local/bin/warp-cli   # ⚠️ 脚本不会删这个软链，会留下死链
```

卸载后验收：`netstat -rn -f inet | grep "^default"` 只剩 1 条；`scutil --dns` 恢复正常。

### Clash Verge / mihomo TUN

- `tun.enable: true` 才会劫持；`false` 时不影响路由（可看
  `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/config.yaml`）
- 系统代理是否开启：`networksetup -getwebproxy Wi-Fi`
- 与 WARP **不要同时开**；两者都想抢默认路由时会互相打乱。

### Tailscale

- DNS 特征 `100.100.100.100`；接口 `utun*` 地址为 `100.x.x.x`
- 状态：`scutil --nc list`

## ⚠️ 踩坑记录

- **别用 App 内的二进制当 CLI**：`/Applications/Tailscale.app/Contents/MacOS/Tailscale status` 会挂住不返回
  （会拉起 GUI）。用 `/usr/local/bin/tailscale` 或带 `timeout` 包裹。
- **`~/Library/Application Support/com.cloudflare.1dot1dot1dot1.macos/` 里的日志文件（root 属主）
  在沙箱环境可能读不到**（`ls` 能列出但 `cat` 报 No such file）。别在这上面耗时间，直接看路由/DNS 就够定性。
- **`pmset -g log | grep Sleep` 可能返回空**，别据此判断"没睡眠"；以 `pmset -g` 里的
  `sleep` 值为准（`0` = 永不休眠）。
- **Mac mini 的有线口常常空着没用**：`en0` 显示 `status: inactive` 就是没插网线，
  此时上网走 `en1`(Wi-Fi)。想更稳就插网线，但**如果网关 ping 是 0 丢包，插网线并不能解决断网**，别误判方向。
- **`ipconfig getpacket en1` 的 `lease_time` 是十六进制**（`0xa8c0` = 43200 秒 = 12 小时），别当十进制读。

## 面向「手机端看不到这台电脑」的额外检查

如果问题表现为手机 App（如 WorkBuddy「我的 Buddy」）里电脑显示离线：

```bash
lsof -nP -iTCP -a -c WorkBuddy | grep -v "127.0.0.1"
```

- 源地址是 `2606:4700:...` 之类**海外/伪造 IPv6** → 云端心跳被隧道拖死，长连接会断
- 出现 `CLOSE_WAIT` → 连接已经死了但进程还攥着，界面就会显示离线
- 修复后应看到**国内 IPv4 + ESTABLISHED**，此时手机端会自行恢复

## 根治原则

**同一时间只允许一个工具接管默认路由。** 代理统一用一个工具（如 Clash Verge `127.0.0.1:7897`），
不要在系统里叠两套隧道。修完后记得写进设备档案，防止下次又装回来。
