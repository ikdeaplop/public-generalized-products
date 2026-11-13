#!/usr/bin/env bash
# publish_github.sh —— 把本地公开仓库发布到 GitHub（幂等，可重跑）
#
# 设计要点：
#   1. 推送前**强制**跑脱敏门禁，不通过就不推（门禁是发布前最后一道闸）
#   2. 创建仓库用 gh（token 已登录），推选用 https（gh 默认协议）
#   3. 幂等：仓库已存在就只推不建；已推送就只补 remote
#   4. **不使用任何 force push** —— 误操作不会毁掉远端历史
#
# 用法：
#   bash scripts/publish_github.sh              # 创建（若不存在）+ 推送
#   bash scripts/publish_github.sh --dry-run    # 只演练，不推
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# 目标仓库：默认占位，发布者用环境变量指定自己的账号
#   REPO_SLUG=your-name/your-repo bash scripts/publish_github.sh
REPO_SLUG="${REPO_SLUG:-your-name/your-repo}"
BRANCH="main"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

cd "$REPO_DIR"

echo "==> [1/5] 脱敏门禁（不通过则中止，不推送）"
bash scripts/check_sensitive.sh

echo
echo "==> [1b/5] 门禁自检（证明门禁本身有效，不只是「当前没命中」）"
bash scripts/gate_selftest.sh

echo
echo "==> [2/5] 仓库状态"
if gh repo view "$REPO_SLUG" >/dev/null 2>&1; then
  echo "   仓库已存在：$REPO_SLUG"
  EXISTS=1
else
  echo "   仓库不存在，待创建：$REPO_SLUG"
  EXISTS=0
fi

echo
echo "==> [3/5] 待推送文件"
git add -A
git diff --cached --name-only | tee /tmp/_pub_files.txt | head -20
echo "   共 $(wc -l < /tmp/_pub_files.txt) 个文件"

if [ "$DRY_RUN" -eq 1 ]; then
  echo
  echo "==> DRY-RUN：到此为止，不建仓不推送"
  exit 0
fi

echo
echo "==> [4/5] 创建并推送"
# ---- 推送通道选择 -------------------------------------------------------
# `git push`（HTTPS）需要账号**已验证邮箱**；账号没设邮箱时会被 403 拒绝。
# `PUT /repos/{owner}/{repo}/contents/{path}`（Contents API）**不看邮箱**，
# 但 GitHub 禁止用它写 `.github/`（平台安全策略 → 404）。
# 所以：优先 git push，失败则降级到 Contents API，并如实报告 .github 缺口。
PUSH_MODE="git"


if [ "$EXISTS" -eq 0 ]; then
  # 注意：`gh repo create` 不支持 --add-topic（那是 `gh repo edit` 的参数）——
  # 实测踩过：unknown flag: --add-topic。topic 在下面 [5] 阶段补。
  gh repo create "$REPO_SLUG" \
    --public \
    --description "Personal collection of general-purpose tools and agent workflow skills (macOS / Python, stdlib-only)."
  echo "   已创建公开仓库：$REPO_SLUG"
fi

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "https://github.com/$REPO_SLUG.git"
else
  git remote add origin "https://github.com/$REPO_SLUG.git"
fi

if git push -u origin "$BRANCH" 2>/tmp/_push_err.txt; then
  PUSH_MODE="git"
  echo "   git push 成功"
else
  echo "   git push 失败：$(tail -2 /tmp/_push_err.txt | tr '\n' ' ' | cut -c1-160)"
  echo "   → 降级到 Contents API（不受邮箱验证限制）"
  PUSH_MODE="api"
  python3 scripts/push_via_api.py --repo "$REPO_SLUG" --branch "$BRANCH" \
    --message "feat: publish repository contents"
fi

echo
echo "==> [5/5] 核验"
# topics（gh repo create 不支持，只能建完再补）
gh repo edit "$REPO_SLUG" \
  --add-topic macos \
  --add-topic python \
  --add-topic agent-skills \
  --add-topic ocr \
  --add-topic automation >/dev/null 2>&1 \
  && echo "   topics 已设置" \
  || echo "   ⚠️ topics 设置失败（不影响发布，可手动补）"

gh repo view "$REPO_SLUG" --json name,visibility,url,description --jq \
  '"   名称: \(.name)\n   可见性: \(.visibility)\n   地址: \(.url)\n   描述: \(.description)"'
echo "   远端分支：$(git ls-remote --heads origin "$BRANCH" 2>/dev/null | awk '{print $2}' | head -1)"
echo "   推送通道：$PUSH_MODE"
if [ "$PUSH_MODE" = "api" ]; then
  echo
  echo "⚠️ 已知限制：Contents API 无法写入 .github/（GitHub 平台禁止），"
  echo "   因此 CI workflow 未上线。补齐方式二选一："
  echo "   a) 在 GitHub 网页设置并验证账号邮箱 → 之后 git push 即可（含 .github/）"
  echo "   b) 手动把 .github/workflows/ci.yml 传到网页端"
fi
echo
echo "✅ 发布完成：https://github.com/$REPO_SLUG"
