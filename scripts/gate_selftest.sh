#!/usr/bin/env bash
# gate_selftest.sh —— 脱敏门禁的**双向**自检
#
# 为什么需要它：
#   「干净树通过」证明不了门禁能抓。只有「植入样本被拦下」才能证明规则有效。
#   而且必须是**逐类**验证 —— 用「退出码非 0」当断言时，一条规则命中会
#   掩盖另一条失效，看起来全绿实则漏检（这正是 self-audit-false-green-trap）。
#
# 用法：bash scripts/gate_selftest.sh
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
PROBE="_gate_probe_tmp.py"
# 探针**不排除**：它必须参与扫描，否则规则根本不会被触发。
FAILED=0

cleanup() { [ -f "$PROBE" ] && rm -f "$PROBE"; }
trap cleanup EXIT

echo "🧪 脱敏门禁双向自检"
echo

# --- 第 1 部分：干净态应通过 ---------------------------------------------
echo "【1】干净态"
if bash scripts/check_sensitive.sh >/dev/null 2>&1; then
  echo "  ✅ 干净树通过"
else
  echo "  ❌ 干净树竟然不通过（门禁有假报）"
  bash scripts/check_sensitive.sh 2>&1 | grep -E '^❌' -A3 | head -20
  FAILED=1
fi
echo

# --- 第 2 部分：逐类植入样本，每类都必须被**对应规则**命中 ---------------
#
# ⚠️ 样本**不能以字面量写在本文件里** —— 门禁扫的是整个仓库，本文件若含
#    被检词的中文/敏感字面量，干净态检查必然失败（自指），且会把所有规则的
#    判定污染成「永远命中」。
#    因此：ASCII 敏感串用变量分片，中文词用 Unicode 码点在运行时拼出。
S_KEY='sk-'"abcdefghijklmnopqrstuvwxyz1234"
S_GHP='ghp_'"ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
S_PW='SuperSecret'"123456"
S_SEC='hunter2'"hunter2hunter2"
S_COOKIE='session='"abcdefghijklmnopqrstuvwxyz0123456789"
S_AUTH='bearer '"abcdefghijklmnopqrstuvwxyz0123"
S_PATH='/Users/'"someone/dir/file.txt"
S_IP='192.168.'"68.239"
S_TS='tail23f989'".ts.net"
S_USER='ikde'"aplop"
# 中文动机词：码点拼装，文件里不出现字面量。
# 注意：**不要用 shell printf 的 \u 转义** —— macOS 的 sh/bash 3.x 不支持，
# 会原样输出 "\u8fd9..." 字面量 → 样本失效。用 python3 构造。
S_MOTIVE1=$(python3 -c 'print("".join(chr(c) for c in [0x8fd9,0x662f,0x8585,0x7f8a,0x6bdb,0x5de5,0x5177]))')
S_MOTIVE2=$(python3 -c 'print("".join(chr(c) for c in [0x652f,0x6301,0x591a,0x8d26,0x53f7,0x7b7e,0x5230]))')
S_MOTIVE3=$(python3 -c 'print("".join(chr(c) for c in [0x7528,0x4e8e,0x6279,0x91cf,0x6ce8,0x518c,0x8d26,0x53f7]))')

# 每项：规则名|样本内容
CASES=(
  "真实密钥|KEY = \"$S_KEY\""
  "真实密钥|TOKEN = \"$S_GHP\""
  "硬编码口令/令牌|PASSWORD = \"$S_PW\""
  "硬编码口令/令牌|SECRET = \"$S_SEC\""
  "Cookie/Session 值|COOKIE = \"$S_COOKIE\""
  "Cookie/Session 值|AUTHORIZATION = \"$S_AUTH\""
  "本机绝对路径|PATH = \"$S_PATH\""
  "内网 IP|HOST = \"$S_IP\""
  "Tailscale/内网域名|HOST = \"$S_TS\""
  "账号标识/项目代号|USER = \"$S_USER\""
  "动机词/风控词|NOTE = \"$S_MOTIVE1\""
  "动机词/风控词|NOTE = \"$S_MOTIVE2\""
  "动机词/风控词|NOTE = \"$S_MOTIVE3\""
)

for c in "${CASES[@]}"; do
  rule="${c%%|*}"
  sample="${c#*|}"
  printf '%s\n' "$sample" > "$PROBE"
  out=$(bash scripts/check_sensitive.sh 2>&1)
  # 规则名可能带补充说明（如「动机词/风控词（...）」），用**前缀匹配**判定，
  # 精确匹配会因括号后缀而漏判 → 自检假红。
  # 且必须点名**探针文件**：只看「该规则是否报了任何东西」会被其它命中掩盖
  # （探针变量名如 HOST= 可能同时触发口令规则）→ 假绿。
  if echo "$out" | grep -A5 "^❌ \[${rule%%（*}" | grep -q "$PROBE"; then
    printf '  ✅ %-18s 命中\n' "$rule"
  else
    printf '  ❌ %-18s **未命中** ← 该规则失效\n' "$rule"
    echo "$out" | grep -E '^❌' | sed 's/^/       /' | head -3
    FAILED=1
  fi
done

cleanup

# --- 第 3 部分：豁免必须真的生效（白名单不该误报）------------------------
echo
echo "【3】白名单豁免"
cat > "$PROBE" <<'P'
MAIL = "someone@example.org"
P
if bash scripts/check_sensitive.sh 2>&1 | grep -q "^❌ \[真实邮箱\]"; then
  echo "  ❌ example.org 被误报 —— 豁免规则失效"
  FAILED=1
else
  echo "  ✅ example.org 被正确豁免"
fi
cleanup

echo
if [ "$FAILED" -ne 0 ]; then
  echo '⛔ 门禁自检未通过 —— 规则存在失效项。'
  exit 1
fi
echo '✅ 门禁自检全部通过：干净态不误报，每类样本都被对应规则命中。'
exit 0
