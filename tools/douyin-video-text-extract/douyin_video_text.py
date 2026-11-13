#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音视频文案提取器（自包含单文件版 · 画面 OCR + 卡片还原 + 口播 ASR）

把「下载视频 → 抽帧 → 识别画面文字 → 还原清单结构 → 合并文字稿」整条链路
压进**一个文件**，拷到任何 Mac 都能跑（只依赖系统自带 + 已装的 patchright/ffmpeg）。

为什么是这个方案（前置结论，别重复趟坑）：
  - yt-dlp / curl 拿抖音 web detail JSON：一律 403（要 X-Bogus 签名）—— 此路已废。
  - <video> 的 src 是 blob:，直接取拿不到真地址。
  - 可行：patchright + 系统 Chrome 监听网络拿 CDN 地址，再用**浏览器自身的
    request context** 下载（自动带登录 cookie，无需手工传 header）。
      —— 来自《2026-10-15-抖音收藏转存夸克：五个坑》已验证路径。
  - 画面文字用 macOS 内置 Vision OCR（零模型下载、中文强）；口播用 mlx-whisper
    （Apple Silicon GPU，本地推理，不出网）。
  - 抖音同一条作品有 media-video（画面）与 media-audio（纯音轨）两个 rendition，
    取错就只下到音频 → ffmpeg 报 "Output file does not contain any stream"。
  - 卡片版面的标题/描述判别**不能用字高**（实测连续无断层），要用「宽度 + 标点
    + 前缀关系」三合一；分列**用左边缘**（间隙实测 0.16+），用中心点会被宽描述行带偏。

用法：
  抖音视频文案提取.py <抖音URL> [--out DIR] [--mode ocr|asr|both]
                        [--eps 1.5] [--model <hf模型>] [--headless] [--keep]
  抖音视频文案提取.py --local <本地视频.mp4> [--out DIR]      # 不联网，直接分析
  抖音视频文案提取.py --selftest                              # 离线自检

产物（默认 ~/Developer/douyin-favorites/transcripts/<aweme_id>/）：
  文字稿.md      画面文字（按帧去重，保留出处帧号）+ 口播文字
  画面文字.tsv   OCR 原始结果（frame/x/y/w/h/text，带归一化坐标）
  清单.md        卡片版面还原出的「平台 | 权益」结构表（检测到卡片版式时才有）
  清单.json      同上，结构化

安全：只读下载与本地识别，不替用户注册/授权任何账号。
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VERSION = "1.0.0"

HOME = Path.home()
DEFAULT_OUT = HOME / "Developer/douyin-favorites/transcripts"
DEFAULT_PROFILE = HOME / "Developer/douyin-favorites/.browser_profile"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
SWIFTC = shutil.which("swiftc") or "/usr/bin/swiftc"
CACHE_DIR = HOME / ".workbuddy/cache/douyin-transcribe"
ASR_VENV = HOME / "Developer/douyin-favorites/.venv-asr/bin/python"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- OCR 工具源码
# macOS Vision OCR：逐帧识别并输出带归一化坐标的 TSV，供上游还原卡片结构。
# 用法：swiftocr <图片目录> <输出tsv>
# 格式：frame_idx \t x \t y \t w \t h \t text
#   x,y = 文字框中心（0-1，原点在左下，Vision 原生）；w,h = 宽高（0-1）
SWIFT_SRC = r'''
import Foundation
import Vision
import AppKit

func ocrImage(_ path: String) -> [(Double, Double, Double, Double, String)] {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return [] }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    req.usesLanguageCorrection = false
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do { try handler.perform([req]) } catch { return [] }
    var out: [(Double, Double, Double, Double, String)] = []
    for o in (req.results ?? []) {
        guard let c = o.topCandidates(1).first else { continue }
        let t = c.string.trimmingCharacters(in: .whitespacesAndNewlines)
        guard t.count >= 2 else { continue }
        let b = o.boundingBox
        out.append((b.midX, b.midY, b.width, b.height, t))
    }
    return out
}

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("用法: swiftocr <图片目录> <输出tsv>\n".data(using: .utf8)!)
    exit(2)
}
let dir = args[1], outPath = args[2]
let fm = FileManager.default
let files = (try? fm.contentsOfDirectory(atPath: dir))?
    .filter { ["png", "jpg", "jpeg", "webp", "heic"].contains(($0 as NSString).pathExtension.lowercased()) }
    .sorted() ?? []

var out = ["# frame\tx\ty\tw\th\ttext"]
var idx = 0
for f in files {
    idx += 1
    for (x, y, w, h, t) in ocrImage(dir + "/" + f) {
        out.append(String(format: "%d\t%.4f\t%.4f\t%.4f\t%.4f\t%@", idx, x, y, w, h, t))
    }
}
try! out.joined(separator: "\n").write(toFile: outPath, atomically: true, encoding: .utf8)
print("[ocr] \(files.count) 帧 -> \(outPath)（\(out.count - 1) 行）")
'''

# ------------------------------------------------------- 卡片还原（版面规律）
# Vision OCR 同音错字纠正（实测样本，可持续补充）
# ⚠️ 只改**经实证**的错字，不确定的宁可留原样（错改成另一个错字更糟）。
FIX = {
    # --- 已实证（对照官方页面/搜索确认过正确写法）---
    "腾云": "腾讯云",          # 腾讯云 TokenHub
    "飞奖": "飞桨",            # 飞桨 EasyDL
    "白度干帆": "百度千帆",     # 百度千帆（OCR 把「百」认成「白」、「千」认成「干」）
    "白度千帆": "百度千帆",     # 同上，仅一字之差
    "Gline": "Cline",          # ClinePass $9.99/月（已查证官方定价）
    "M Plan 承接个人订阅": "MiniMax M Plan 承接个人订阅",  # Go/Explore/Build 三档
    "智增": "智谱",
    "商阂": "商汤", "商沕": "商汤",
    "商问": "商汤",
    "飞楽": "飞桨",
    "浆 EasyDL": "飞桨 EasyDL",
    "干帆": "千帆",
    "沙稍": "沙箱", "炒稍": "沙箱", "纱稍": "沙箱", "少箱": "沙箱",
    "免費": "免费",
    "默以": "默认", "井发": "并发",
    "赛愽": "赛博",
    "槛代金券": "门槛代金券",
    "怡工": "移工", "涨量": "账量",
    "水久": "永久", "领成": "领取",
    "扎": "扎",
    # --- 英文：Al（OCR 把大写 I 认成小写 l）---
    "OpenAl": "OpenAI",
    "Agnes Al": "Agnes AI",
    "Tomosu Al": "Tomosu AI",
    "WebScraping.Al": "WebScraping.AI",
    "App Al Gateway": "App AI Gateway",
    "Novita Al": "Novita AI",
    "Notion Al": "Notion AI",
    "Comty Gloud": "Comfy Cloud",   # Comfy Cloud 免费层送5次真实 GPU 运行（已查证官网定价页）
    "Comty Cloud": "Comfy Cloud",
    "火ll": "Firefly",
    # --- 英文：其余品牌错字（2026-11-13 补，实测原 FIX 接不住）---
    # 实测 fix_text 对以下 8 条 MISS，全部来自 11-12 某科技资讯视频帧31/帧41 版面实证。
    # 注意 fix_text 是**子串替换**，所以这里只放最短词根，长串留给第二级 PLAT_FIX。
    "GrOg": "Grok",              # xAI Grok，帧41 列4 标题行
    "Tavily Al": "Tavily",      # Tavily，帧48/50 列4 标题行（原文实测为「Tavily Al」，有空格）
    "Browse Al": "Browse AI",    # Browse AI，帧51 列2 标题行（同类 l→I）
    "KirO": "Kiro",              # 大小写错，Kiro（AWS 出的 AI IDE），帧54 列4
    "WorkBuddy Hy4": "WorkBuddy",  # 标题混入版本号后缀（帧54 列1）
}

# 需要正则精确匹配的替换（键是值的子串时，纯 str.replace 会二次替换）。
# 格式：(正则, 替换文本)。**先于 FIX 执行**。
FIX_RE = [
    (r"Workato(?! Free)", "Workato Free"),
]
# 页面装饰 / 噪声，不是内容条目
NOISE = re.compile(
    r"最值得拿|还能领|已整理资料|查看简介|每天更新|◎|数据核验"
    r"|^\d{1,2}/\d{1,2}$|抖音号|关注|点赞|收藏|分享|评论"
    # 封面页排版文字（实测帧1/帧2：'今天值得看'、'条重点'、'本用清樂130 ※・固内4/日外86'、
    # '今天最值得拿的是Novita'）——封面是整页排版，不是卡片网格，全是噪声
    r"|今天值得看|今天最值得拿|条重点|本用清樂|^\d{2}/\d{2}$|^0\d$|^\d{1,2}$")

# 「平台名被权益文字污染」的切分规则（实测：标题框把平台名和首行权益连在了一起）。
# 格式：(平台名前缀, 权益起始标记)。命中则把标记之前的部分留作平台名、
# 标记及之后的原文移到权益开头。**每条都是对着真实 OCR 输出确认过的**。
SPLIT_RULES = [
    ("Workato Free", "注册送"),
    ("秒哒", "免费版每天登录送"),
    ("数眼智能", "注册绑手机号送"),
    ("华为云码道", "把积分计费个人"),
    ("ZCode", "每天送"),
    ("Tavily", "每月"),
    ("Workers AI", "每天免费"),
    ("Browse AI", "免费档"),
    ("Plaud", "各方案一次性免费发"),
    ("Novita Sandbox", "新账号免费"),
    ("Google 开发者计划", "免费会"),
    ("Google开发者计划", "免费会"),
    ("App AI Gateway", "每月"),
    ("Notion Business", "30天试用"),
]


def log(msg):
    print(msg, flush=True)


def fix_text(s):
  # 先走正则精确替换：处理「键是值的子串」导致的二次替换污染。
  # 实测（帧57 列0）：标题行「Workato」需补成「Workato Free」，
  # 但同一列下一行原文就是「Workato Free 注册送」，
  # 纯 str.replace 会把它改成「Workato Free Free 注册送」。
  # ⇒ 用负向断言只在「后面不是 Free」时才替换。
  for pat, rep in FIX_RE:
    s = re.sub(pat, rep, s)
  for a, b in FIX.items():
    s = s.replace(a, b)
  return s.strip()


def load_rows(path):
    """读 OCR TSV → [(frame, x, y, w, h, text)]"""
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        parts = ln.split("\t")
        if len(parts) < 6:
            continue
        try:
            rows.append((int(parts[0]), float(parts[1]), float(parts[2]),
                         float(parts[3]), float(parts[4]), parts[5]))
        except ValueError:
            continue
    return rows


def cluster_columns(items, gap=0.06):
    """按文本框左边缘聚类成列（同一列卡片左边缘对齐）。"""
    items = sorted(items, key=lambda it: it[0] - it[3] / 2)
    cols, cur = [], []
    for it in items:
        left = it[0] - it[3] / 2
        if cur and left - cur[-1][0] > gap:
            cols.append(cur)
            cur = []
        cur.append((left,) + it)
    if cur:
        cols.append(cur)
    return cols


def is_title(t, nxt, w):
    """标题判别（实测版面：标题窄、描述宽且带「：」或「…」）。
    优先级：
      1. 以 …/⋯ 结尾 → 描述（卡片文本被截断）
      2. 含「：」→ 描述（权益说明句）
      3. 下一行含本行特征词 → 标题（最强信号，如 TRAE → "TRAE 注册即送…"）
      4. 很短（≤12 字）→ 标题
      5. 又宽又长（w≥0.145 且 >14 字）→ 描述
      6. 其余按长度兜底
    """
    if not t or NOISE.search(t):
        return False
    if t.endswith(("…", "⋯", "...")):
        return False
    if "：" in t or ":" in t:
        return False
    if nxt:
        toks = [x for x in re.split(r"[\s·、，,/（）()]+", t) if len(x) >= 2]
        if t in nxt or (toks and (toks[-1] in nxt or toks[0] in nxt)):
            return True
        if ("：" in nxt or ":" in nxt) and ("：" not in t and ":" not in t) and len(t) <= 20:
            return True
    if len(t) <= 12 and w < 0.125:
        return True
    if w >= 0.145 and len(t) > 14:
        return False
    # 兜底：只有「窄行」才可能是标题。
    # 实测（帧41 列0）：Mistral 标题 w=0.0578，其下一行「学生价＄5.99/月订阅」
    # w=0.1347 是描述行 —— 旧兜底 len(t)<=20 把它当标题，导致 Mistral 整条丢失。
    # 窄行阈值 0.125 取自实测：真标题 w≤0.11，"Google 开发者计划" w=0.1382
    # 这类偏宽的标题靠「下一行含本行词」的强信号命中，不依赖兜底。
    return len(t) <= 20 and w < 0.125


def noise_body(s):
    """正文像乱码/纯数字符号 → 判为噪声。"""
    good = sum(1 for ch in s if ch.isalnum() or "一" <= ch <= "鿿")
    return good < len(s) * 0.55


def split_platform(title, body):
    """把「平台名+首行权益」连在一起的标题切开，还原成 (平台名, 权益)。

    实测：OCR 常把卡片标题和它的第一行权益识别成一个文本框，例如
    「Workato Free 注册送」+「50,000 credits（一次性、」→「Workato Free」
    +「注册送50,000 credits（一次性、」。用 SPLIT_RULES 逐条对真实输出校准。
    """
    for head, marker in SPLIT_RULES:
        if title.startswith(head) and marker in title[len(head):]:
            rest = title[len(head):]
            pos = rest.index(marker)
            plat = title[:len(head)]
            moved = rest[pos:]
            return plat, (moved + body).strip()
    return title, body


def parse_cards(rows):
    """把 OCR 结果还原成「平台 | 权益」卡片列表。

    版面绝大多数是「标题在上、权益在下」（正向）。按列聚类 → 标题/描述判别 → 配对。

    ⚠️ 已知局限（试过两种修法都失败，别再重试）：
    少数卡片是**跨帧接续**的长卡片——标题在上一帧底部、权益在下一帧顶部
    （实测帧51/54 的「阿拉伯地区18岁以上在校大学」「1万名科学家可申请」
    「美国认证医护（MD/DO、」「早期到 A轮初创公司可申请最」4 条）。
    试过 ①单帧内向上找宽行 ②相邻帧同列配对 —— **都会跨卡片误归属**
    （实测把 Snill 标题配到 OpenRouter 的权益、把 CodeGPT 配到 Google），
    因为列索引在帧间会漂移、且同列内多张卡片的宽行会串。
    ⇒ 结论：**单帧正向规则最稳**，跨帧卡片靠「已知条目补全表」人工兜底，
    不在通用解析里硬解。宁可少 4 条，不要错配 20 条。
    """
    byf = {}
    for f, x, y, w, h, t in rows:
        t = fix_text(t)
        if not t or NOISE.search(t):
            continue
        byf.setdefault(f, []).append((x, y, w, h, t))
    cards = []
    for f in sorted(byf):
        for col in cluster_columns(byf[f]):
            col.sort(key=lambda it: -it[2])
            # ---- 标题被 OCR 拆成两行时合并（必须在**同列内**做）--------
            # 实测（帧34 列1）：标题「上海人工智能实验室（书生・端砚）」被 OCR 切成
            # 宽行「上海人工智能实验室（书生・」(w=0.1828) + 窄行「端砚）」(w=0.0391)，
            # 结果宽行被当描述、窄行被当标题 → 平台名只剩「端砚）」。
            # ⚠️ 不能在全局 y 排序里做：同帧其它列的行会插在中间（实测帧34 有 4 列）。
            # 判据：行内「（」比「）」多（未闭合）→ 下一行是续行 → 合并。
            # cluster_columns 返回 (left, x, y, w, h, text) 6 元组
            merged = []
            k = 0
            while k < len(col):
                it = col[k]
                t = it[5]
                open_paren = ((t.count("（") > t.count("）")) or
                              (t.count("(") > t.count(")")))
                if open_paren and k + 1 < len(col):
                    nxt = col[k + 1]
                    # 续行可能是「端砚）」这种「先有字、后有右括号」的形式，
                    # 不一定以右括号开头 → 判据：续行含右括号且不含左括号。
                    nt = nxt[5]
                    if ("）" in nt or ")" in nt) and "（" not in nt and "(" not in nt:
                        merged.append((it[0], it[1], it[2], it[3] + nxt[3], it[4],
                                       it[5] + nxt[5]))
                        k += 2
                        continue
                merged.append(it)
                k += 1
            col = merged
            seq = [(it[5], it[3]) for it in col]
            i = 0
            while i < len(seq):
                nxt = seq[i + 1][0] if i + 1 < len(seq) else None
                if not is_title(seq[i][0], nxt, seq[i][1]):
                    i += 1
                    continue
                title = seq[i][0]
                desc = []
                j = i + 1
                while j < len(seq):
                    nxt2 = seq[j + 1][0] if j + 1 < len(seq) else None
                    if is_title(seq[j][0], nxt2, seq[j][1]):
                        break
                    desc.append(seq[j][0])
                    j += 1
                body = "".join(desc).strip()
                # 标题长度上限放到 40：实测「上海人工智能实验室（书生·端砚）」
                # 有 17 字，26 会把它截断成两行（渲染出「上海人工智能实验室（书生・」）。
                if title and len(body) >= 5 and len(title) <= 40 and not noise_body(body):
                    title2, body2 = split_platform(title, body)
                    cards.append({"frame": f, "platform": title2, "benefit": body2})
                i = max(j, i + 1)
    seen, uniq = set(), []
    for c in cards:
        # 去重 key 用「平台名 + 权益前 25 字」，但平台名归一化后（去空白），
        # 避免同一张卡因 OCR 微差（如「书生・端砚）」vs「书生·端砚）」）重复入库。
        # 权益也要归一：同一张卡在不同帧的 OCR 微差（如「书生•端砚墨点计划：测试期每」
        # vs「书生•端砚墨点计划：测试期每月」）会漏过去重。
        k = (re.sub(r"[\s·・]+", "", c["platform"]),
             re.sub(r"[\s⋯…、，,。：:；;（）()【】\[\]「」\-–—·•]+", "", c["benefit"])[:30])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    return uniq


# ------------------------------------------------------------------ 基础能力
def aweme_id(url):
    m = re.search(r"/(?:video|note|share/video)/(\d+)", url or "")
    return m.group(1) if m else None


def has_stream(path, kind="v"):
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-select_streams",
                            ("v:0" if kind == "v" else "a:0"),
                            "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(path)], capture_output=True, text=True, timeout=30)
        return ("video" if kind == "v" else "audio") in r.stdout
    except Exception:
        return False


def probe_duration(path):
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                            "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def ensure_ocr_tool():
    """把内嵌 Swift 源码编译成 OCR 小工具（缓存复用，源变了才重编）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    src = CACHE_DIR / "swiftocr.swift"
    binp = CACHE_DIR / "swiftocr"
    if not src.exists() or src.read_text(encoding="utf-8") != SWIFT_SRC:
        src.write_text(SWIFT_SRC, encoding="utf-8")
    if not binp.exists() or binp.stat().st_mtime < src.stat().st_mtime:
        if not SWIFTC or not Path(SWIFTC).exists():
            raise RuntimeError("找不到 swiftc，无法编译 OCR 工具")
        log("   编译 OCR 工具…")
        subprocess.run([SWIFTC, "-O", str(src), "-o", str(binp)],
                       check=True, timeout=300)
    binp.chmod(0o755)
    return binp


def extract_frames(video, frames_dir, eps):
    frames_dir.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
           "-vf", "fps=1/%.2f,scale='min(1280,iw)':-2" % eps, "-q:v", "3",
           str(frames_dir / "f_%04d.jpg")]
    subprocess.run(cmd, check=True, timeout=600)
    return sorted(frames_dir.glob("*.jpg"))


def run_ocr(frames_dir, out_tsv):
    tool = ensure_ocr_tool()
    subprocess.run([str(tool), str(frames_dir), str(out_tsv)], check=True, timeout=1800)
    return load_rows(out_tsv)


def build_report(rows, meta):
    """按帧还原可读文字稿（去重、保留出处帧号）"""
    by = {}
    for f, x, y, w, h, t in rows:
        by.setdefault(f, []).append((y, x, t))
    out = ["# 抖音视频文字稿 · %s\n" % meta.get("aid"),
           "- 视频：%s\n- 时长：%s 秒\n- 抓取：%s\n" % (
               meta.get("url"), meta.get("dur"), meta.get("ts")),
           "\n## 一、画面文字（OCR · macOS Vision · zh-Hans+en）\n"]
    seen = set()
    for f in sorted(by):
        for y, x, t in sorted(by[f], key=lambda r: (-r[0], r[1])):
            if t in seen:
                continue
            seen.add(t)
            out.append("[帧%d] %s" % (f, t))
    out.append("\n## 二、口播文字（ASR · mlx-whisper）\n")
    out.append(meta.get("asr") or "（本轮未跑 ASR）")
    return "\n".join(out) + "\n"


def run_asr(video, out_txt, model):
    """口播转写。需要 .venv-asr 环境与已下好的 mlx-community/whisper-* 模型。"""
    if not ASR_VENV.exists():
        raise RuntimeError("ASR 环境缺失：%s（pip install mlx-whisper）" % ASR_VENV)
    wav = video.with_suffix(".16k.wav")
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                   check=True, timeout=600)
    try:
        code = (
            "import sys, mlx_whisper;"
            "r = mlx_whisper.transcribe(sys.argv[1], path_or_hf_repo=sys.argv[3],"
            " language='zh', verbose=False);"
            "open(sys.argv[2], 'w', encoding='utf-8').write(r['text'])"
        )
        subprocess.run([str(ASR_VENV), "-c", code, str(wav), str(out_txt), model],
                       check=True, timeout=7200,
                       env={**os.environ, "HF_HUB_DISABLE_XET": "1"})
    finally:
        wav.unlink(missing_ok=True)
    return Path(out_txt).read_text(encoding="utf-8")


# ------------------------------------------------------------------ 下载层
def _pick_video_url(ad):
    """从 aweme_detail 取**视频轨**直链（media-video=画面；media-audio=纯音轨）。"""
    v = (ad or {}).get("video") or {}
    cands = []
    for key in ("play_addr_h264", "play_addr_265", "play_addr"):
        for u in ((v.get(key) or {}).get("url_list") or []):
            cands.append(u)
    for br in v.get("bit_rate") or []:
        for u in ((br.get("play_addr") or {}).get("url_list") or []):
            cands.append(u)
    vids = [u for u in cands if "media-video" in u or "video/tos" in u]
    auds = [u for u in cands if "media-audio" in u or "audio/tos" in u]
    return vids + [u for u in cands if u not in auds] + auds


def _pick_audio_url(ad):
    """取音轨直链（有些视频画面 rendition 不带音轨，需单独取）。"""
    v = (ad or {}).get("video") or {}
    for br in v.get("bit_rate_audio") or []:
        ul = (br.get("audio_meta") or {}).get("url_list") or {}
        u = ul.get("main_url") or ((ul.get("backup_url") or [None])[0])
        if u:
            return u
    return None


async def download(url, out, headless, profile=DEFAULT_PROFILE):
    """浏览器内截获页面自己签好的 detail JSON → 取视频轨 → 浏览器 request context 下载。"""
    from patchright.async_api import async_playwright
    for n in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (profile / n).unlink(missing_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=headless, executable_path=CHROME,
            args=["--no-proxy-server", "--disable-blink-features=AutomationControlled"])
        page = await ctx.new_page()
        cands, detail = [], []

        page.on("response", lambda r: cands.append(r.url)
                if ("douyinvod" in r.url or "/aweme/v1/play/" in r.url)
                and 200 <= r.status < 400 else None)

        async def _cap(r):
            if "/aweme/v1/web/aweme/detail/" in r.url:
                try:
                    detail.append(await r.json())
                except Exception:
                    pass
        page.on("response", _cap)

        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(9000)
        await page.mouse.move(720, 400)
        await page.mouse.click(720, 400)
        await page.wait_for_timeout(7000)

        ad = None
        for j in detail:
            if isinstance(j, dict) and j.get("aweme_detail"):
                ad = j["aweme_detail"]
                break
        urls = _pick_video_url(ad) if ad else []
        urls += [u for u in cands if "media-video" in u]
        seen, ordered = set(), []
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                ordered.append(u)
        if not ordered:
            await ctx.close()
            raise RuntimeError("没抓到视频地址（页面可能未登录 / 网络异常）")

        got = 0
        for target in ordered[:6]:
            log("  试地址: " + target[:110] + "...")
            try:
                resp = await page.request.get(target, headers={
                    "Referer": "https://www.douyin.com/", "User-Agent": UA,
                    "Range": "bytes=0-"})
                body = await resp.body()
            except Exception as e:
                log("    失败: " + type(e).__name__)
                continue
            if len(body) < 10240:
                log("    太小（%d B），跳过" % len(body))
                continue
            out.write_bytes(body)
            vid = has_stream(out, "v")
            log("    HTTP %s，%.2f MiB，视频轨=%s" % (resp.status, len(body) / 1048576, vid))
            if vid:
                got = len(body)
                break
        await ctx.close()
        if not got:
            raise RuntimeError("所有候选地址都没拿到可用视频流")
        return got


# ------------------------------------------------------------------ 主流程
async def process(video, work, a, aid, url, meta):
    rows, asr_txt = [], ""
    if a.mode in ("both", "ocr"):
        log("② 抽帧 + 画面 OCR…")
        frames = extract_frames(video, work / "frames", a.eps)
        log("   抽到 %d 帧" % len(frames))
        if not frames:
            raise RuntimeError("抽帧 0 帧（视频可能损坏或不是视频轨）")
        rows = run_ocr(work / "frames", work / "画面文字.tsv")
        log("   OCR 文本 %d 行" % len(rows))
        if not rows:
            raise RuntimeError("OCR 0 行（假绿守卫：抽帧有图却认不出字）")
        cards = parse_cards(rows)
        if cards:
            lines = ["# 画面清单结构化（%d 条）\n" % len(cards),
                     "| # | 标题 | 内容 | 帧 |", "|---|---|---|---|"]
            for i, c in enumerate(cards, 1):
                lines.append("| %d | %s | %s | %d |" % (i, c["platform"], c["benefit"], c["frame"]))
            (work / "清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            (work / "清单.json").write_text(
                json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
            log("   清单还原 %d 条 -> 清单.md" % len(cards))
        else:
            log("   未检出卡片版式，跳过清单还原")

    if a.mode in ("both", "asr"):
        log("③ 口播 ASR…")
        src = video
        if not has_stream(video, "a"):
            alt = work / "audio_only.mp4"
            if alt.exists():
                src = alt
                log("   画面流无音轨，改用 %s" % alt.name)
        asr_txt = run_asr(src, work / "口播.txt", a.model)
        log("   ASR 文本 %d 字" % len(asr_txt))

    meta["asr"] = asr_txt
    (work / "文字稿.md").write_text(build_report(rows, meta), encoding="utf-8")
    log("✅ 文字稿 -> %s" % (work / "文字稿.md"))
    return {"ocr_rows": len(rows), "asr_chars": len(asr_txt),
            "cards": len(parse_cards(rows)) if rows else 0}


async def main():
    ap = argparse.ArgumentParser(description="抖音视频文案提取（画面 OCR + 口播 ASR）")
    ap.add_argument("url", nargs="?", help="抖音视频 URL")
    ap.add_argument("--local", help="本地视频文件（不联网）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--mode", default="ocr", choices=["both", "ocr", "asr"])
    ap.add_argument("--eps", type=float, default=1.5, help="抽帧间隔秒")
    ap.add_argument("--model", default="mlx-community/whisper-base-mlx")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--keep", action="store_true", help="保留视频与帧（默认清理以省磁盘）")
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.local:
        aid = Path(a.local).stem
        url = "file://" + str(Path(a.local).resolve())
    else:
        if not a.url:
            ap.error("需要抖音 URL，或用 --local 指定本地视频，或 --selftest")
        aid = aweme_id(a.url)
        if not aid:
            log("❌ URL 里没解析出 aweme_id")
            return 2
        url = a.url

    work = Path(a.out) / aid
    work.mkdir(parents=True, exist_ok=True)
    video = work / "video.mp4"
    meta = {"aid": aid, "url": url,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "dur": 0}

    if a.local:
        src = Path(a.local)
        if not src.exists():
            log("❌ 本地文件不存在：%s" % src)
            return 2
        if not video.exists() or video.stat().st_size != src.stat().st_size:
            shutil.copy2(src, video)
        log("① 使用本地视频 %s" % src)
    else:
        if not video.exists():
            log("① 下载视频…")
            n = await download(a.url, video, a.headless, Path(a.profile))
            log("   下载 %.2f MiB" % (n / 1048576))
        else:
            log("① 复用已下载视频 %s" % video)

    meta["dur"] = round(probe_duration(video), 1)
    log("   时长 %.1fs" % meta["dur"])

    summary = await process(video, work, a, aid, url, meta)

    if not a.keep:
        shutil.rmtree(work / "frames", ignore_errors=True)
        if not a.local:
            video.unlink(missing_ok=True)
            log("   已清理视频与帧（--keep 可保留）")

    print(json.dumps({"aid": aid, "work": str(work), **summary}, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ 自检
SELFTEST_TSV = """# frame\tx\ty\tw\th\ttext
31\t0.1031\t0.8403\t0.0625\t0.0308\t阶跃星辰
31\t0.1586\t0.7963\t0.1740\t0.0395\tStepAudio 3系列（实时语
31\t0.1656\t0.7556\t0.1844\t0.0333\t音、语音合成、音频生成、…
31\t0.1266\t0.7042\t0.1063\t0.0306\t阿里 Qoder CN
31\t0.1680\t0.6625\t0.1891\t0.0306\t匿名模型 Qoduck 每日免费额
31\t0.1633\t0.6250\t0.1828\t0.0333\t度内不扣 Credits、每天领⋯
31\t0.3500\t0.8361\t0.1064\t0.0354\t智谱 BigModel
31\t0.3859\t0.7944\t0.1781\t0.0333\tGLM Coding Plan：三档每
31\t0.3383\t0.7597\t0.0859\t0.0250\t5小时积分⋯
31\t0.3422\t0.7083\t0.0908\t0.0347\t火山引擎方舟
31\t0.3734\t0.6625\t0.1531\t0.0306\tCoding Plan 限时折扣：
31\t0.3914\t0.6236\t0.1891\t0.0306\tDeepSeek V4.1 Flash可用⋯
31\t0.0914\t0.1889\t0.0578\t0.0225\t已整理资料
31\t0.1953\t0.1889\t0.1250\t0.0222\t查看简介或评论区指引
"""


def selftest():
    ok = []

    def chk(name, cond, extra=""):
        ok.append(bool(cond))
        print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                             ("  → " + str(extra)) if extra else ""))

    print("== 抖音视频文案提取器 selftest v%s ==" % VERSION)

    with tempfile.TemporaryDirectory() as td:
        tsv = Path(td) / "t.tsv"
        tsv.write_text(SELFTEST_TSV, encoding="utf-8")

        rows = load_rows(tsv)
        chk("TSV 解析", len(rows) == 14, "%d 行" % len(rows))

        chk("aweme_id 解析", aweme_id("https://www.douyin.com/video/7707083579544702223")
            == "7707083579544702223")
        chk("aweme_id 无效 URL 返回 None", aweme_id("https://x.com/abc") is None)

        chk("错字映射 沙稍→沙箱", fix_text("沙稍环境") == "沙箱环境")
        chk("噪声过滤 已整理资料", bool(NOISE.search("已整理资料")))

        # 2026-11-13 新增映射：键必须与真实 OCR 原文逐字一致，否则永不命中
        for raw, want in (("GrOg", "Grok"), ("Tavily Al", "Tavily"),
                          ("Browse Al", "Browse AI"), ("KirO", "Kiro"),
                          ("Workato", "Workato Free"),
                          ("WorkBuddy Hy4", "WorkBuddy")):
            chk("错字映射 %s→%s" % (raw, want), fix_text(raw) == want,
                fix_text(raw))
        # 键是值的子串时不能二次替换（帧57 列0：标题「Workato」/ 下一行「Workato Free 注册送」）
        chk("Workato 不二次替换", fix_text("Workato Free 注册送") == "Workato Free 注册送",
            fix_text("Workato Free 注册送"))
        # 幂等：已修正的值再跑一次不能变
        chk("修正幂等", fix_text("Browse AI") == "Browse AI")

        # 与 parse_cards 保持同一顺序：先滤噪声再分列（夹具覆盖真实数据的前 2 列）
        clean = [(r[1], r[2], r[3], r[4], r[5]) for r in rows
                 if not NOISE.search(fix_text(r[5]))]
        cols = cluster_columns(clean)
        chk("左边缘分列（夹具 2 列）", len(cols) == 2, "%d 列" % len(cols))

        chk("标题判别：宽且带冒号→描述",
            not is_title("GLM Coding Plan：三档每", None, 0.1781))
        chk("标题判别：省略号结尾→描述",
            not is_title("音、语音合成、音频生成、…", None, 0.1844))
        chk("标题判别：短词→标题", is_title("阶跃星辰", None, 0.0625))

        cards = parse_cards(rows)
        chk("卡片还原出条目", len(cards) >= 4, "%d 条" % len(cards))
        plats = " | ".join(c["platform"] for c in cards)
        chk("含平台 阶跃星辰", "阶跃星辰" in plats, plats[:80])
        chk("含平台 智谱 BigModel", "智谱 BigModel" in plats)
        chk("含平台 火山引擎方舟", "火山引擎方舟" in plats)
        chk("权益含 StepAudio",
            any("StepAudio" in c["benefit"] for c in cards))
        chk("噪声条目未进清单", not any("已整理资料" in c["platform"] for c in cards))

        mp = Path(td) / "文字稿.md"
        mp.write_text(build_report(rows, {"aid": "x", "url": "u", "dur": 1, "ts": "t"}),
                      encoding="utf-8")
        chk("文字稿生成", mp.exists() and "画面文字" in mp.read_text(encoding="utf-8"))

    print("---- %d/%d ----" % (sum(ok), len(ok)))
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
