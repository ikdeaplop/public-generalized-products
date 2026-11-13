#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context_cost.py — 读文件前先估 token 成本，避免"一次误读烧掉几万 token"

启发式估算：中文字符 ≈1.1 token/字，其余字符 ≈0.25 token/字符（近似 claude tokenizer）。

用法:
  python3 context_cost.py <file-or-dir> [...]
  python3 context_cost.py --top 20 ~/.workbuddy/skills        # 列出目录里最大的 20 个文件
  python3 context_cost.py --audit-skill <skill-dir>           # 审计单个 skill 的上下文开销

判定阈值:
  < 2k token   → 可直接 Read
  2k-8k token  → 有针对性再读，优先 Grep 定位
  > 8k token   → 禁止整读；改用脚本 parse / Grep + Read(offset,limit)
"""
import os
import re
import sys

CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
SCAN_EXT = {".md", ".txt", ".json", ".py", ".js", ".ts", ".html", ".css",
            ".yaml", ".yml", ".csv", ".xml", ".sql", ".sh"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build"}


def est_tokens(text):
    cjk = len(CJK.findall(text))
    return int(cjk * 1.1 + (len(text) - cjk) * 0.25)


def verdict(tok):
    if tok < 2000:
        return "OK 直接读"
    if tok < 8000:
        return "谨慎 先Grep定位"
    return "禁止整读"


def scan_file(path):
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        return None
    text = raw.decode("utf-8", errors="ignore")
    return {"path": path, "bytes": len(raw), "tokens": est_tokens(text)}


def collect(targets):
    out = []
    for t in targets:
        t = os.path.expanduser(t)
        if os.path.isfile(t):
            r = scan_file(t)
            if r:
                out.append(r)
        elif os.path.isdir(t):
            for root, dirs, files in os.walk(t):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in files:
                    if os.path.splitext(f)[1].lower() in SCAN_EXT:
                        r = scan_file(os.path.join(root, f))
                        if r:
                            out.append(r)
    return sorted(out, key=lambda x: -x["tokens"])


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    top = None
    if "--top" in sys.argv:
        i = sys.argv.index("--top")
        try:
            top = int(sys.argv[i + 1])
            args = [a for a in args if a != sys.argv[i + 1]]
        except (IndexError, ValueError):
            pass

    if not args:
        print(__doc__)
        return 0

    rows = collect(args)
    rows = rows[:top] if top else rows
    if not rows:
        print("未找到可扫描的文件")
        return 0

    total = sum(r["tokens"] for r in rows)
    w = max(len(r["path"]) for r in rows)
    w = min(w, 70)
    print(f"{'文件':<{w}}  {'字节':>9}  {'估算token':>9}  判定")
    print("-" * (w + 34))
    for r in rows:
        p = r["path"]
        p = "..." + p[-(w - 3):] if len(p) > w else p
        print(f"{p:<{w}}  {r['bytes']:>9}  {r['tokens']:>9}  {verdict(r['tokens'])}")
    print("-" * (w + 34))
    print(f"合计 {len(rows)} 个文件 / 估算 {total} token（全部读完的代价）")
    over = [r for r in rows if r["tokens"] >= 8000]
    if over:
        print(f"\n⚠️  {len(over)} 个文件超 8k token，禁止整读：")
        for r in over:
            print(f"   {r['tokens']:>7}  {r['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
