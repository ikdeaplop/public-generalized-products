#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompt_profiler.py — 上下文开销剖析器

两个用途:
  A. 分析真实 prompt 样本，量化「固定开销占比」（可缓存/可模板化的部分）
  B. 分析埋点 CSV，产出各场景基线（P50/P95、月总消耗、优化优先级排序）

用法:
  # A. prompt 样本分析
  python3 prompt_profiler.py <file-or-dir>              # 分析 .txt/.md/.json prompt 样本
  python3 prompt_profiler.py <dir> --min-dup 3          # 重复判定窗口 ≥3 行（默认 3）
  python3 prompt_profiler.py <dir> --verbose            # 展开重复片段原文

  # B. 埋点日志分析
  python3 prompt_profiler.py --log metrics.csv
  python3 prompt_profiler.py --log metrics.csv --days 14 --price-in 0.003 --price-out 0.015
  python3 prompt_profiler.py --init-template metrics_template.csv

判定口径:
  固定开销 = 在 ≥2 个样本中重复出现、且长度 ≥min-dup 行的连续块 → 这部分可走
  Prompt Caching / 模板化 / 知识库外置，是优化的第一优先级。
"""
import argparse
import csv
import os
import re
import sys
from collections import defaultdict

CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
SAMPLE_EXT = {".txt", ".md", ".json", ".log", ".prompt"}


def est_tokens(t):
    c = len(CJK.findall(t))
    return int(c * 1.1 + (len(t) - c) * 0.25)


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "—"


# ─────────── A. prompt 样本分析 ───────────
def load_samples(target):
    files = []
    if os.path.isfile(target):
        files = [target]
    else:
        for root, dirs, fs in os.walk(target):
            dirs[:] = [d for d in dirs if d not in {"node_modules", ".git"}]
            files += [os.path.join(root, f) for f in fs
                      if os.path.splitext(f)[1].lower() in SAMPLE_EXT]
    out = []
    for f in files:
        try:
            out.append((f, open(f, encoding="utf-8", errors="ignore").read()))
        except OSError:
            pass
    return sorted(out)


def dup_blocks(samples, min_dup):
    """找跨样本重复的连续行块。

    返回 (dups, meta, covered)：
      dups    {block_text: {file, ...}}        仅保留出现在 ≥2 个样本的块
      meta    {block_text: [(file, 起始行1基)]}
      covered {file: set(行号0基)}             被任一重复块覆盖的行（已去重，用于算真实固定开销）
    """
    windows = defaultdict(set)
    meta = {}
    spans = defaultdict(list)   # block -> [(file, start0, end0)]
    for path, text in samples:
        lines = [l.rstrip() for l in text.split("\n")]
        for i in range(len(lines) - min_dup + 1):
            blk = "\n".join(lines[i:i + min_dup])
            if not blk.strip() or len(blk.strip()) < 20:
                continue
            windows[blk].add(path)
            meta.setdefault(blk, []).append((path, i + 1))
            spans[blk].append((path, i, i + min_dup))

    dups = {k: v for k, v in windows.items() if len(v) >= 2}
    # 按行去重：滑动窗口之间高度重叠，必须合并行号再统计，否则会算超 100%
    covered = defaultdict(set)
    for blk in dups:
        for path, s, e in spans[blk]:
            covered[path].update(range(s, e))
    return dups, meta, covered


def analyze_prompts(target, min_dup, verbose):
    samples = load_samples(target)
    if not samples:
        print("未找到 prompt 样本（支持 .txt/.md/.json/.log）")
        return 1

    print("=" * 78)
    print(f"Prompt 样本剖析 · {len(samples)} 个样本")
    print("=" * 78)

    total_tokens = 0
    rows = []
    for path, text in samples:
        t = est_tokens(text)
        total_tokens += t
        rows.append((path, t, len(text.split("\n"))))
    for path, t, ln in rows:
        print(f"  {t:>8} tok  {ln:>5} 行  {os.path.basename(path)}")
    print("-" * 78)
    print(f"  {total_tokens:>8} tok  合计（{len(samples)} 个样本）")

    dups, meta, covered = dup_blocks(samples, min_dup)
    if not dups:
        print("\n未检测到跨样本重复的连续块 → 样本间差异大，固定开销低，")
        print("优化重点应转向「单次输入内部的冗余」（如大知识库整量注入）。")
        return 0

    # 真实固定开销：按行去重后统计，避免滑动窗口重叠导致重复计数
    line_cache = {}
    fixed_tokens = 0
    for path, text in samples:
        lines = [l.rstrip() for l in text.split("\n")]
        line_cache[path] = lines
        fixed_tokens += sum(est_tokens(lines[i]) for i in covered[path])
    n = len(samples)
    # 优化后重复部分只保留一份 → 可省 (1 - 1/n)
    total_saved = fixed_tokens * (1 - 1 / n) if n > 1 else 0

    print(f"\n{'=' * 78}")
    print(f"固定开销：{len(dups)} 个重复块 · 按行去重后 {fixed_tokens:,} tok "
          f"（{n} 个样本各带一份）")
    print(f"若走 Prompt Caching / 模板化，可省 ≈ {total_saved:,.0f} tok/轮 · "
          f"占样本总量 {pct(total_saved, total_tokens)}")
    print("=" * 78)

    # 展示：合并重叠块，只显示互不覆盖的大块（贪心）
    scored = sorted(
        ((est_tokens(blk) * (len(fs) - 1), est_tokens(blk), len(fs), blk)
         for blk, fs in dups.items()), reverse=True)
    shown, used = 0, defaultdict(set)
    for saved, t, cnt, blk in scored:
        if shown >= 8:
            break
        fs = dups[blk]
        first = meta[blk][0]
        # 滑动窗口彼此错开 1 行，起始行已展示过即跳过，避免刷屏
        s0 = first[1] - 1
        if s0 in used[first[0]]:
            continue
        used[first[0]].update(range(s0, s0 + min_dup))
        shown += 1
        print(f"\n  [≈{t:,} tok × {cnt} 个样本]  首见于 "
              f"{os.path.basename(first[0])}:{first[1]}")
        preview = blk if len(blk) <= 130 else blk[:130] + " …"
        for line in preview.split("\n")[:4]:
            print(f"      {line[:100]}")
        if verbose and len(blk) > 130:
            print(f"      （完整 {len(blk)} 字符）")

    print(f"\n{'=' * 78}")
    print("建议（按优先级）:")
    print("  1. 把上述重复块固化为 prompt 前缀 → 走 Prompt Caching，命中部分单价大降")
    print("  2. 若重复块是知识库/字段表 → 外置为检索脚本，按需注入命中的 N 条")
    print("  3. 若重复块是指令 → 抽成模板，只把变量放末尾（保持前缀字节稳定）")
    return 0


# ─────────── B. 埋点日志分析 ───────────
def p(vals, q):
    if not vals:
        return 0
    s = sorted(vals)
    i = min(int(len(s) * q), len(s) - 1)
    return s[i]


def analyze_log(path, days, pin, pout):
    if not os.path.exists(path):
        print(f"文件不存在: {path}")
        return 1
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("CSV 为空")
        return 1
    need = {"scene", "t_in", "t_out"}
    if not need.issubset({c.strip() for c in rows[0].keys()}):
        print(f"CSV 缺少必需列 {need}；用 --init-template 生成模板")
        return 1

    by = defaultdict(lambda: {"in": [], "out": [], "lat": [], "n": 0})
    for r in rows:
        sc = (r.get("scene") or "unknown").strip()
        try:
            ti = int(float(r["t_in"]))
            to = int(float(r["t_out"]))
        except (TypeError, ValueError, KeyError):
            continue
        lat = 0
        try:
            lat = float(r.get("latency_ms") or 0)
        except ValueError:
            pass
        d = by[sc]
        d["in"].append(ti)
        d["out"].append(to)
        d["lat"].append(lat)
        d["n"] += 1

    print("=" * 96)
    print(f"埋点基线分析 · {len(rows)} 条记录 · 观测 {days} 天")
    print("=" * 96)
    hdr = (f"{'场景':<20}{'次数':>7}{'输入P50':>9}{'输入P95':>9}{'输出P50':>9}"
           f"{'输出P95':>9}{'延迟P95':>9}{'月总tok':>13}")
    print(hdr)
    print("-" * 96)

    results = []
    for sc, d in sorted(by.items(), key=lambda x: -sum(x[1]["in"]) - sum(x[1]["out"])):
        ti50, ti95 = p(d["in"], .5), p(d["in"], .95)
        to50, to95 = p(d["out"], .5), p(d["out"], .95)
        lat95 = p(d["lat"], .95)
        per_call = (sum(d["in"]) + sum(d["out"])) / d["n"]
        monthly = per_call * (d["n"] / days * 30)
        results.append((sc, d["n"], ti50, ti95, to50, to95, lat95, monthly, per_call))
        print(f"{sc[:19]:<20}{d['n']:>7}{ti50:>9}{ti95:>9}{to50:>9}{to95:>9}"
              f"{lat95:>9.0f}{monthly:>13,.0f}")
    print("-" * 96)

    tot_month = sum(r[7] for r in results)
    tot_calls = sum(r[1] for r in results)
    print(f"{'合计':<20}{tot_calls:>7}{'':>45}{tot_month:>13,.0f}")

    if pin or pout:
        cost = 0
        for r in results:
            sc = r[0]
            d = by[sc]
            cost += (sum(d["in"]) / d["n"] * (d["n"] / days * 30)) * (pin or 0) / 1000
            cost += (sum(d["out"]) / d["n"] * (d["n"] / days * 30)) * (pout or 0) / 1000
        print(f"\n月成本估算（输入 ¥{pin}/1k · 输出 ¥{pout}/1k）：¥{cost:,.0f}")

    print(f"\n优化优先级（按月总消耗排序）:")
    cum = 0
    for i, r in enumerate(results, 1):
        cum += r[7]
        print(f"  {i}. {r[0]:<22} {r[7]:>12,.0f} tok/月  "
              f"累计占比 {pct(cum, tot_month):>6}  单次均值 {r[8]:,.0f} tok")
    print("\n判读：累计占比到 80% 的那几个场景，就是 ROI 最高的优化目标（帕累托）。")
    return 0


TEMPLATE = """scene,t_in,t_out,latency_ms,timestamp
商机判断,1200,180,820,2026-08-31 09:00
商机判断,1500,210,910,2026-08-31 09:01
客户建联,2400,760,3200,2026-08-31 09:05
询盘回复,1800,900,2400,2026-08-31 09:10
线索挖掘,5200,1600,15000,2026-08-31 09:20
客户背调,14000,2400,48000,2026-08-31 09:40
"""


def main():
    ap = argparse.ArgumentParser(description="上下文开销剖析器")
    ap.add_argument("target", nargs="?", help="prompt 样本文件或目录")
    ap.add_argument("--log", help="埋点 CSV 路径")
    ap.add_argument("--init-template", metavar="PATH", help="生成埋点 CSV 模板")
    ap.add_argument("--min-dup", type=int, default=3, help="重复判定窗口行数，默认 3")
    ap.add_argument("--days", type=float, default=1, help="埋点观测天数，用于推算月总量")
    ap.add_argument("--price-in", type=float, default=0, help="输入单价 元/1k token")
    ap.add_argument("--price-out", type=float, default=0, help="输出单价 元/1k token")
    ap.add_argument("--verbose", action="store_true", help="展开重复片段原文")
    a = ap.parse_args()

    if a.init_template:
        open(a.init_template, "w", encoding="utf-8").write(TEMPLATE)
        print(f"模板已生成: {a.init_template}")
        print("字段: scene, t_in, t_out, latency_ms, timestamp（latency_ms/timestamp 可留空）")
        return 0
    if a.log:
        return analyze_log(a.log, a.days, a.price_in, a.price_out)
    if a.target:
        return analyze_prompts(a.target, a.min_dup, a.verbose)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
