#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
slc_check.py — 语义无损压缩（SLC）交付前自检门

对比「原文」与「压缩稿」，自动核查最容易被压缩误伤的**硬信息**是否守恒：
  数字（缺失/新增）· 禁用类 · 例外类 · 条件类 · 模态最高强度 · URL/邮箱/文件路径
  · 全大写标识符 · 型号代号 · 引号内容 · 一般否定

⚠️ 边界说明：本脚本只做**守恒校验**（该留的有没有留下、不该有的有没有多出来），
   **不判定语义等价**。"留下同样的词"不等于"留下同样的意思"，反之亦然。
   等价性仍需人工过 SKILL.md 里的 4 问。
   命中 FAIL 时先回退重压该段，**不要为了让脚本变绿去改数字**。

启发式口径与 context_cost.py 一致（中文 1.1 token/字，其余 0.25 token/字符）。

用法:
  python3 slc_check.py 原文.txt 压缩稿.txt
  python3 slc_check.py 原文.md 压缩稿.md --shots     # 打印命中明细（去重后的词表）
  python3 slc_check.py --text "原文……" "压缩稿……"
  python3 slc_check.py 原文 压缩稿 --json            # 机器可读

退出码: 0 = 通过（可能带 WARN）；1 = 存在 FAIL，必须回退重压
"""
import json
import re
import sys

CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def est_tokens(text):
    """与 context_cost.py 同一套启发式，保证两个脚本口径一致。"""
    cjk = len(CJK.findall(text))
    return int(cjk * 1.1 + (len(text) - cjk) * 0.25)


# --- 1. 数字 ---------------------------------------------------------------
NUM = re.compile(r"\d+(?:[,\uff0c]\d{3})*(?:\.\d+)?")
# 列表编号不是信息，压缩时重排编号属正常，须排除
LIST_MARK = re.compile(r"(?:(?<=^)|(?<=\n))\s*\d+[.\u3001\uff0e]\s|\d+[)\uff09]")


def norm_num(s):
    s = s.replace(",", "").replace("\uff0c", "")
    try:
        f = float(s)
        return str(int(f)) if f.is_integer() else str(f)
    except ValueError:
        return s


def numbers(text):
    """返回 {归一化数字: 次数}，已剔除列表编号。"""
    skip = set()
    for m in LIST_MARK.finditer(text):
        for n in NUM.finditer(m.group()):
            skip.add((m.start() + n.start(), m.start() + n.end()))
    out = {}
    for m in NUM.finditer(text):
        if (m.start(), m.end()) in skip:
            continue
        n = norm_num(m.group())
        out[n] = out.get(n, 0) + 1
    return out


# --- 2. 语义词桶（长词优先匹配，避免"不得"被"不"截胡）----------------------
# 先屏蔽这些词里的歧义单字，避免"免费/非常/可能/可见"被误判成否定词或模态词
NOISE = ["非常", "免费", "可能", "未知", "未来", "别人", "特别", "区别",
         "若干", "可靠", "许可", "认可", "可见", "可行", "可控", "可口",
         "不可或缺",
         # 含"应"但非模态的常见词（避免裸"应"误判为强制级）
         "应客户", "应对", "相应", "对应", "适应", "供应", "应聘", "回应",
         "响应", "反应", "应用", "效应", "应急", "因应", "照应", "应邀"]


def mask(text):
    for w in NOISE:
        text = text.replace(w, "\u0001" * len(w))
    return text


BUCKETS = {
    "禁用类": ["禁止", "严禁", "不得", "不许", "不准", "不要", "杜绝", "切勿"],
    "例外类": ["除非", "除外", "例外", "除了"],
    "条件类": ["如果", "倘若", "一旦", "否则", "只要", "只有", "前提是", "前提",
               "若", "\u2192", "\u21d2", "\u27f6"],
    "一般否定": ["不", "没", "无", "未", "否"],
}

# 模态强度分级：数字越大越强。"须"与"必须"同为强制级，"可"与"可以"同为允许级。
# "需要"刻意不计入——它在外贸/口语文本里多为填充词，纳入会大量误报。
MODAL = {
    4: ["必须", "务必", "一定要", "须", "应当", "应该", "应",
        "严禁", "不得", "禁止", "不要", "不许", "不准"],
    3: ["建议", "推荐", "最好", "优先"],
    2: ["可以", "允许", "支持", "可"],
    1: ["可选", "视情况", "按需", "酌情"],
}
MODAL_NAME = {4: "强制", 3: "建议", 2: "允许", 1: "可选"}


def build_alt(words):
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


# 词 → 归属桶（长词优先，保证"不得"归禁用类而不是被"不"截胡）
WORD2BUCKET = {w: b for b, ws in BUCKETS.items() for w in ws}
BUCKET_ALT = build_alt(WORD2BUCKET)


def bucket_hits(text):
    """一次扫描把所有桶词归位：每个命中只计一次，杜绝跨桶重复计数。"""
    out = {b: {} for b in BUCKETS}
    for m in re.finditer(BUCKET_ALT, text):
        b = WORD2BUCKET[m.group()]
        out[b][m.group()] = out[b].get(m.group(), 0) + 1
    return out


def modal_levels(text):
    out = {}
    for lvl, words in MODAL.items():
        n = len(re.findall(build_alt(words), text))
        if n:
            out[lvl] = n
    return out


# --- 3. 标识符 -------------------------------------------------------------
IDENT = [
    ("URL", re.compile(r"https?://[^\s)\]\uff0c\u3002\u3001]+")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("文件/路径", re.compile(
        r"(?:[\w./-]*/)?[\w.-]+\.(?:py|js|ts|tsx|md|json|ya?ml|csv|xlsx?|docx?|"
        r"pptx?|zip|html?|css|sh|txt|sql|png|jpe?g|env)\b")),
    ("全大写标识符", re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")),
    ("型号/代号", re.compile(r"\b[A-Za-z]{1,4}[-\u2010]?\d{2,}[A-Za-z]*\b")),
]
SOFT_IDENT = {"全大写标识符", "型号/代号"}  # 命中只报 WARN，避免英文大写词噪声

QUOTE_PAIRS = re.compile(
    r"[\u201c\uff02\u300c\u300e]([^\u201d\uff02\u300d\u300f]{2,})[\u201d\uff02\u300d\u300f]")


def idents(text):
    out = {}
    for label, pat in IDENT:
        found = {m.group().rstrip(".,;:\u3001\u3002\uff0c") for m in pat.finditer(text)}
        if found:
            out[label] = found
    return out


def quotes(text):
    return {m.group(1).strip() for m in QUOTE_PAIRS.finditer(text)}


# --- 4. 主流程 -------------------------------------------------------------
def check(orig, comp):
    fails, warns, infos, rows = [], [], [], []

    # 4.1 数字
    n_o, n_c = numbers(orig), numbers(comp)
    missing = sorted(set(n_o) - set(n_c), key=lambda x: (len(x), x))
    added = sorted(set(n_c) - set(n_o), key=lambda x: (len(x), x))
    dropped = sorted(k for k in (set(n_o) & set(n_c)) if n_c[k] < n_o[k])
    if missing:
        fails.append(("数字缺失", "原文中的数字在压缩稿里找不到：" + "、".join(missing)))
    if added:
        fails.append(("数字新增", "压缩稿出现原文没有的数字（疑似脑补或擅自换算）："
                                + "、".join(added)))
    if dropped:
        warns.append(("数字次数减少", "以下数字出现次数变少（多半是合并了重复项，需人工确认）："
                      + "、".join(f"{k}({n_o[k]}→{n_c[k]})" for k in dropped)))
    rows.append(("数字", f"{len(set(n_o)) - len(missing)}/{len(set(n_o))}"
                        if n_o else "无",
                 not missing and not added))

    # 4.2 语义词桶
    mo, mc = mask(orig), mask(comp)
    bo, bc = bucket_hits(mo), bucket_hits(mc)
    for name in ("禁用类", "例外类", "条件类", "一般否定"):
        hits_o = bo[name]
        to, tc = sum(hits_o.values()), sum(bc[name].values())
        ok = to == tc
        if not ok:
            detail = (f"原文 {to} 处 → 压缩稿 {tc} 处"
                      + (f"（原文用词：{'、'.join(hits_o)}）" if hits_o else ""))
            if to > 0 and tc == 0:
                fails.append((f"{name}消失", detail))
            elif tc < to * 0.5:
                warns.append((f"{name}大幅减少", detail))
        rows.append((name, f"{to}→{tc}" if to or tc else "无", ok))

    # 4.3 模态强度（只比最高强度；强度升降级都算违约）
    lo, lc = modal_levels(mo), modal_levels(mc)
    max_o = max(lo) if lo else 0
    max_c = max(lc) if lc else 0
    if max_o != max_c:
        d = "升级" if max_c > max_o else "降级"
        fails.append((f"模态强度{d}",
                      f"原文最高 {max_o}({MODAL_NAME.get(max_o, '无')}) → "
                      f"压缩稿最高 {max_c}({MODAL_NAME.get(max_c, '无')})"
                      "；'可选/建议/允许/强制' 四档不可升降"))
    rows.append(("模态最高强度",
                 f"{max_o}({MODAL_NAME.get(max_o, '无')}) → {max_c}({MODAL_NAME.get(max_c, '无')})",
                 max_o == max_c))

    # 4.4 标识符
    io, ic = idents(orig), idents(comp)
    all_labels = sorted(set(io) | set(ic))
    for label in all_labels:
        lost = sorted(io.get(label, set()) - ic.get(label, set()))
        rows.append((label, "无" if not io.get(label) else
                     f"{len(io[label]) - len(lost)}/{len(io[label])}", not lost))
        if not lost:
            continue
        joined = "、".join(lost)
        if label in SOFT_IDENT:
            warns.append((f"{label}缺失", joined))
        else:
            fails.append((f"{label}缺失", joined))

    # 4.5 引号内容
    qo, qc = quotes(orig), quotes(comp)
    lost_q = sorted(qo - qc)
    hard_q = [q for q in lost_q
              if len(q) >= 10 or re.search(r"\d", q) or re.search(r"[A-Za-z]{2,}", q)]
    soft_q = [q for q in lost_q if q not in hard_q]
    if hard_q:
        fails.append(("引号内容缺失", "、".join(f"\u300c{q}\u300d" for q in hard_q[:6])
                      + ("…" if len(hard_q) > 6 else "")))
    if soft_q:
        warns.append(("引号内容缺失(短)", "、".join(soft_q[:6])
                      + "（若只是强调语气可接受，需人工确认）"))
    if qo:
        rows.append(("引号内容", f"{len(qo) - len(lost_q)}/{len(qo)}", not hard_q))

    # 4.6 压缩比
    co, cc = len(orig), len(comp)
    ratio = (co - cc) / co * 100 if co else 0.0
    if ratio < 0:
        warns.append(("压缩比为负", f"压缩稿比原文长 {abs(co - cc)} 字符，"
                                   "检查是否误加了标题、解释或代码围栏"))
    elif ratio < 5 and co > 200:
        infos.append(("压缩比偏低", f"仅 {ratio:.1f}%。原文可能已接近最优，"
                                   "可考虑直接告知用户无需压缩"))

    return {
        "stats": {
            "chars_orig": co, "chars_comp": cc,
            "tokens_orig": est_tokens(orig), "tokens_comp": est_tokens(comp),
            "ratio_pct": round(ratio, 1),
            "saved_tokens": est_tokens(orig) - est_tokens(comp),
        },
        "conservation": [{"item": a, "value": b, "ok": c} for a, b, c in rows],
        "fails": fails, "warns": warns, "infos": infos,
        "pass": not fails,
    }


def read_arg(a):
    if a.startswith("@"):
        a = a[1:]
    if os.path.isfile(a):
        return open(a, encoding="utf-8", errors="ignore").read()
    return a


def main():
    argv = sys.argv[1:]
    as_json = "--json" in argv
    left = [a for a in argv if not a.startswith("--")]

    if "--text" in argv:
        rest = argv[argv.index("--text") + 1:][:2]
        if len(rest) < 2:
            print(__doc__)
            return 0
        orig, comp = rest
    elif len(left) >= 2:
        orig, comp = read_arg(left[0]), read_arg(left[1])
    else:
        print(__doc__)
        return 0

    r = check(orig, comp)

    if as_json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["pass"] else 1

    s = r["stats"]
    print("语义无损压缩 · 自检报告")
    print("=" * 46)
    print(f"原文    {s['chars_orig']:>7,} 字符   估算 {s['tokens_orig']:>7,} token")
    print(f"压缩稿  {s['chars_comp']:>7,} 字符   估算 {s['tokens_comp']:>7,} token")
    print(f"压缩比  {s['ratio_pct']:>6.1f}%          预计节省 {s['saved_tokens']:,} token/次")
    print()
    print("硬信息守恒")
    for row in r["conservation"]:
        print(f"  {'OK ' if row['ok'] else '!! '}{row['item']:<12} {row['value']}")

    if r["fails"]:
        print(f"\n[FAIL] {len(r['fails'])} 项 —— 必须回退重压")
        for name, detail in r["fails"]:
            print(f"  · {name}：{detail}")
    if r["warns"]:
        print(f"\n[WARN] {len(r['warns'])} 项 —— 需人工抽查")
        for name, detail in r["warns"]:
            print(f"  · {name}：{detail}")
    if r["infos"]:
        print(f"\n[INFO] {len(r['infos'])} 项")
        for name, detail in r["infos"]:
            print(f"  · {name}：{detail}")

    print()
    print("通过：压缩稿未发现硬信息丢失或新增。" if r["pass"]
          else "未通过：存在硬信息问题，回退重压该段或原样保留原句。")
    print("提示：本脚本只做守恒校验，语义等价仍需人工过 4 问"
          "（数字专名否定条件 / 有无脑补 / 反向还原 / 有无歧义）。")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
