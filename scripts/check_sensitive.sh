#!/usr/bin/env bash
# check_sensitive.sh —— 公开仓库发布前脱敏自检
#
# 任何一条命中即 exit 1。可直接接 CI（见 .github/workflows/ci.yml）。
# 用法：bash scripts/check_sensitive.sh [仓库根目录]
#
# 设计原则：
#   1. 零依赖（只用 grep/sed），任何人 clone 下来都能直接跑
#   2. 白名单化——example.com / @anthropic 等公开标识不误报
#   3. 命中即阻断，不做「警告但放行」

set -uo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT" || { echo "❌ 无法进入仓库根目录: $ROOT"; exit 1; }

FAILED=0
EXCL=(--exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=node_modules
      --exclude-dir=.venv --exclude-dir=dist --exclude-dir=build
      --exclude=check_sensitive.sh   # 排除本脚本自身：规则文本会匹配到自己
      --exclude=stage_public.py)     # 排除同步脚本：其正则示例含被检模式字面量

# 自检时把探针文件排除，避免「探针本身触发其它规则」污染判定。
# 形如 CHECK_SENSITIVE_EXTRA_EXCLUDE="_probe*.py"
if [ -n "${CHECK_SENSITIVE_EXTRA_EXCLUDE:-}" ]; then
  for pat in $CHECK_SENSITIVE_EXTRA_EXCLUDE; do
    EXCL+=("--exclude=$pat")
  done
fi

hit() {
  local name="$1"; shift
  local pattern="$1"; shift

  # ---- 具名参数：--ignore-case / --skip <regex> ----------------------------
  # 早期版本用位置参数（flags 在第 3 位、skip 在第 4 位），但很多调用点只传了
  # 2 个参数 → skip 被当成 flags 读走，**豁免规则静默失效**（假绿）。
  # 改成具名参数后，位置错不了；缺省就是「不豁免、区分大小写」。
  local flags=""
  local skip=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --ignore-case) flags="-i" ;;
      --skip) shift; skip="${1:-}" ;;
      *) printf '❌ hit(): 未知参数 %s（规则：%s）\n' "$1" "$name"; FAILED=1 ;;
    esac
    shift
  done

  local out
  out=$(grep -rInE $flags "$pattern" "${EXCL[@]}" . 2>/dev/null)
  # ⚠️ 豁免正则必须匹配 grep 的**完整输出格式**：`文件:行号:内容`。
  #    写成 `file.sh:PATTERN` 会被行号挡住 → 豁免静默失效（规则照报）。
  #    正确写法：`file\.sh:[0-9]+:PATTERN`
  if [ -n "$skip" ] && [ -n "$out" ]; then
    out=$(printf '%s\n' "$out" | grep -vE "$skip")
  fi
  if [ -n "$out" ]; then
    FAILED=1
    printf '\n❌ [%s] 发现敏感内容：\n' "$name"
    printf '%s\n' "$out" | head -20
    local n; n=$(printf '%s\n' "$out" | wc -l | tr -d ' ')
    [ "$n" -gt 20 ] && printf '   ...（共 %s 处，已显示前 20）\n' "$n"
  fi
}

echo "🔍 脱敏自检：$ROOT"

hit "真实密钥" \
  'sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|gho_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AIza[0-9A-Za-z_-]{35,}|xai-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|sk-ant-[A-Za-z0-9_-]{20,}'

hit "硬编码口令/令牌" \
  '(password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{12,}["'"'"']' \
  --ignore-case

hit "Cookie/Session 值" \
  '(cookie|session|jwt|authorization)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{24,}["'"'"']' \
  --ignore-case

hit "私钥文件" \
  'BEGIN [A-Z ]*PRIVATE KEY'

hit "本机绝对路径" \
  '/Users/[a-zA-Z0-9._-]+/'

hit "内网 IP" \
  '\b192\.168\.[0-9]{1,3}\.[0-9]{1,3}\b|\b10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b'

hit "Tailscale/内网域名" \
  '\b[a-z0-9-]+\.ts\.net\b' \
  --skip 'Selection-and-Redaction-Principles\.md:[0-9]+:.*\*\.ts\.net'

hit "真实邮箱" \
  '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' \
  --skip 'example\.(com|org|net)|@anthropic\.com|@openai\.com|@github\.com|@jetbrains\.com|@gnu\.org|localhost'

hit "账号标识/项目代号" \
  '\bikdeaplop\b|\bscgzsk\b|\bbypy\b' \
  --skip 'publish_github\.sh:[0-9]+:REPO_SLUG='

# ---------------------------------------------------------------------------
# 动机词 / 风控词
#
# 这不是「敏感信息」，而是**框架性风险**：如果一个仓库的公开文案通篇是
# 「薅羊毛 / 签到 / 多账号 / 换额度」这类表述，平台审核会把它归类为薅羊毛工具，
# 进而影响账号本身。README 里写下的红线必须由门禁来强制，否则只是一句承诺。
#
# 规则要点：
#   1. 只扫**公开可见文案**（md / py / sh / yml / txt / json）——
#      代码里的功能性正则关键词若需要保留，用 allow 清单豁免具体行。
#   2. 命中即阻断 —— 与其它规则一致，不做「警告但放行」。
#   3. 词表要短而准：宁漏不滥，避免把正常技术表述误杀。
#   4. 规则名用中性表述 —— 门禁的**输出**会被贴到 issue / PR 里，
#      规则名本身不该暴露被检词。
# ---------------------------------------------------------------------------
hit "动机词/风控词（公开文案不得暴露不当意图）" \
  '羊毛|薅|多账号|签到|换额度|额度通道|申请通道|批量注册|自动注册账号' \
  --ignore-case \
  --skip 'build-actionable-list\.py:[0-9]+:.*白嫖'

# 凭据类文件名：只匹配**真实存在的文件**，不扫文本提及。
# 注意：.gitignore 里必然列出这些文件名（否则就防不住它们），
# 若扫文本会导致「规则与忽略清单自相矛盾」→ 每次都红。
find . -type f \
  \( -name '.freellm-keys.conf' -o -name '.machine-identity.json' \
     -o -name '.hf_token' -o -name '*.key' -o -name '*.pem' \
     -o -name '*.p12' -o -name '*.pfx' -o -name '*.keystore' \) \
  -not -path './.git/*' -print >/tmp/_cred_files.txt 2>/dev/null
if [ -s /tmp/_cred_files.txt ]; then
  FAILED=1
  printf '\n❌ [凭据类文件存在] 发现不该入库的文件：\n'
  head -20 /tmp/_cred_files.txt
fi

if [ "$FAILED" -ne 0 ]; then
  echo ''
  echo '⛔ 脱敏自检未通过 —— 禁止发布。'
  exit 1
fi

echo ''
echo '✅ 脱敏自检通过：未发现敏感内容。'
exit 0
