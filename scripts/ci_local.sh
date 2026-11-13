#!/usr/bin/env bash
# ci_local.sh —— run every check that the hosted CI workflow runs, locally.
#
# Why this exists
# ---------------
# GitHub refuses to create files under `.github/workflows/` for accounts whose
# email address is not verified (the API answers 404 for that whole subtree,
# whatever the file name).  Until that account-level requirement is satisfied,
# the workflow definition cannot live on the remote — but every check it
# performs can still be run here, on the author's machine, before every push.
#
# The two are deliberately identical in substance: this script is the local
# mirror of `.github/workflows/ci.yml`, step for step.
#
# Usage:
#   bash scripts/ci_local.sh          # run all checks, non-zero exit on failure
#   bash scripts/ci_local.sh --list   # print the step names and exit
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

if [ "${1:-}" = "--list" ]; then
  cat <<'EOF'
1  Redaction check            scripts/check_sensitive.sh
2  Self-test (ocr tool)       tools/douyin-video-text-extract/douyin_video_text.py --selftest
3  Self-test (version tool)   tools/script-version-keeper.py --selftest
4  Syntax check (python)      every .py under tools/ and scripts/
5  Skill structure check      every skills/<name>/SKILL.md has a name field
6  Syntax check (shell)       bash -n on every scripts/*.sh
7  Gate self-test             scripts/gate_selftest.sh
EOF
  exit 0
fi

FAILED=0
step() { printf '\n==> [%s/7] %s\n' "$1" "$2"; }
fail() { echo "❌ FAILED: $1"; FAILED=1; }

step 1 "Redaction check"
if bash scripts/check_sensitive.sh; then
  echo "✓ redaction gate passed"
else
  fail "redaction gate reported hits (see above)"
fi

step 2 "Self-test — douyin_video_text.py"
if python3 tools/douyin-video-text-extract/douyin_video_text.py --selftest; then
  echo "✓ douyin_video_text.py selftest passed"
else
  fail "douyin_video_text.py selftest failed"
fi

step 3 "Self-test — script-version-keeper.py"
if python3 tools/script-version-keeper.py --selftest; then
  echo "✓ script-version-keeper.py selftest passed"
else
  fail "script-version-keeper.py selftest failed"
fi

step 4 "Syntax check — all Python files"
py_fail=0
while IFS= read -r -d '' f; do
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$f" \
    || { echo "❌ syntax error: $f"; py_fail=1; }
done < <(find tools scripts -name '*.py' -print0)
[ "$py_fail" -eq 0 ] && echo "✓ python syntax check passed" || fail "python syntax check"

step 5 "Skill structure check"
sk_fail=0
for d in skills/*/; do
  name="$(basename "$d")"
  if [ ! -f "$d/SKILL.md" ]; then
    echo "❌ missing SKILL.md: $name"; sk_fail=1; continue
  fi
  if ! head -20 "$d/SKILL.md" | grep -q '^name:'; then
    echo "❌ SKILL.md has no name field: $name"; sk_fail=1
  fi
done
[ "$sk_fail" -eq 0 ] && echo "✓ skill structure check passed" || fail "skill structure check"

step 6 "Syntax check — shell scripts"
sh_fail=0
for f in scripts/*.sh; do
  bash -n "$f" || { echo "❌ shell syntax error: $f"; sh_fail=1; }
done
[ "$sh_fail" -eq 0 ] && echo "✓ shell syntax check passed" || fail "shell syntax check"

step 7 "Gate self-test — planted samples must be caught"
if bash scripts/gate_selftest.sh; then
  echo "✓ gate self-test passed (the gate catches what it claims to catch)"
else
  fail "gate self-test failed (the gate is not proven to work)"
fi

printf '\n'
if [ "$FAILED" -eq 0 ]; then
  echo "✅ all 7 local CI checks passed"
  exit 0
fi
echo "❌ local CI failed — do not publish until these are green"
exit 1
