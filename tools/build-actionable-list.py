#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从短视频画面清单里筛出**账号类免费权益**，生成可执行的《待领清单》。

为什么要筛：原始卡片里「平台 | 权益」是纯信息列表，**无法直接行动**。
真正可领的都需要真人动作（注册 / OAuth / 实名 / 绑卡 / 身份验证），
得按**门槛类型**分组才能照单勾选。

门槛分档（从低到高）：
  L0 免门槛   —— 注册即得，不要卡不要验证（但仍要注册账号）
  L1 需注册   —— 需账号 + 可能需填问卷/加群
  L2 需实名   —— 企业实名 / 个人实名认证
  L3 需绑卡   —— 需绑定支付方式（绑卡后可能扣费，务必看清）
  L4 需身份   —— 学生/教师/医护/军人等身份验证（本人资质才可领）

用法：
  python3 生成待领清单.py <清单.json> [-o 输出.md] [--json 输出.json]
"""
import argparse
import json
import re
import sys
from collections import defaultdict

# ---- 门槛识别关键词（基于真实清单文案实测，逐条对过）------------------
RULES = [
    # L4 身份资质验证
    ("L4", [
        ("学生", r"学生|在校|K-12|在校学生"),
        ("教师", r"教师|教师版"),
        ("医护", r"医护|MD/DO|NP、PA|药师"),
        ("军人", r"军人|ID\.me|退役"),
        ("科学家", r"科学家"),
        ("创公司", r"初创公司|startup|创始人"),
    ]),
    # L3 绑卡 / 支付方式（注意「免绑卡/免信用卡」不算，见下方反向处理）
    ("L3", [
        ("绑卡", r"(?<!免)绑卡|(?<!免)绑.{0,4}卡|信用卡|支付方式|(?<!不)用绑卡|绑.{0,4}支付|卡号|开通支付"),
        ("付费", r"付费购买|需购买|购买后|下单即"),
    ]),
    # L2 实名认证
    ("L2", [
        ("实名", r"实名|身份认证|企业认证|营业执照|组织认证|资质"),
    ]),
    # L1 注册类门槛
    ("L1", [
        ("注册", r"注册|开户|激活|登录|领取需|报名|加群|问卷|填写"),
        ("邀请", r"邀请|邀1人|邀好友|邀请码|referral"),
        ("学生验证", r"验证学生身份|验证身份|认证教师"),
    ]),
]

# L0 反向判据：明确写了「免绑卡/免信用卡/无需验证」→ 门槛最低
FREE_NO_CARD = re.compile(r"免信用卡|免绑卡|不用绑卡|无需信用卡|无需信|免信|免验证|无需验证")

# ---- 替代通道表（2026-11-13 实证）----------------------------------------
# 背景：L4「需身份」的条目里，**很多并不是真的只有学生一条路**。
# 每条给出「不用学生身份也能走的路」，或明确「本条无替代 / 地区不符」。
# 依据：各平台官方页面 + WebSearch 当期政策，详见
#       transcripts/<aweme_id>/身份通道决策表.md
ALT_CHANNEL = {
    "JetBrains": "免验证通道：8 个 IDE 有社区版免费版（IntelliJ/PyCharm/CLion/WebStorm/RustRover/Rider/RubyMine/DataGrip）；"
                 "完整 Student Pack 另可用 GitHub Student Pack 一键领",
    "Google": "免验证通道：Gemini API 免费层与学生身份无关，AI Studio 建 key 即用、不绑卡；"
              "学生版 Google AI Pro 限美/日/韩/西 且需绑卡（到期 $19.99/月）",
    "Base44": "免验证通道：免费层直接注册即用（5 应用 / 25 message credits /月），"
              "初创计划需 VC/加速器背书或直接申请 Builder 版",
    "阿里云云工开物": "教师通道：高校教师认证（人工审核 2 个工作日）→ QoderWork CN 额外 4,000 Credits + 百炼 5 折（补贴上限 20 万）；"
                      "学生通道：支付宝查学信网学籍 → 300 元无门槛抵扣金",
    "GitHub": "教师通道：education.github.com/teachers → Copilot Pro 免费（1,500 credits/月，可手选模型）；"
              "开源维护者通道：个人菜单→Copilot settings 直接查，GitHub 自动评估、每月重估",
    "OpenAI": "教师通道：ChatGPT 教师版经 SheerID 验证，限美国 K-12、免至 2028-06（明确不适用学生）；"
              "医护/军人通道需 ID.me 身份验证",
    "Kiro": "免绑卡但限校：Kiro Students 需 132 所指定大学之一 + SheerID 一次性验证，"
            "1,000 credits/月×12 月；不在名单可申请校加入；早期公司通道走初创申请",
    "Microsoft": "Azure for Students：需 18 岁+、全日制、学位授予制院校 + 机构邮箱；"
                 "MOOC/非学位/营利培训一律排除；**每人终身仅一次**，激活前务必确认",
    "CapCut": "Creative Campus 仅美/英/法/德/日 5 国的指定合作高校项目，国内基本无解；"
              "官方另有限时 7 天 Pro 试用与 PC 邀请码（每人 7 天，上限 70 天）",
    "百度千帆": "实名认证即可（个人），新用户送 20 元无门槛代金券，有效期 1 个月；"
                "两档抵扣金不是一回事，注意区分",
    "Cerebras": "绑卡后若不免费即按量计费；领完额度立刻在控制台删除支付方式，或用虚拟卡",
}

# 判定「这条到底有没有可领的东西」——必须命中其中之一才算数。
# （之前的判据漏掉了「对实名」「验证学生身份后」「新账号 Free 计划」这类写法）
HAS_REAL_BENEFIT = re.compile(
    r"免费|额度|积分|credits|Credits|Token|tokens|送|领|试用|赠|返还|白拿|0积分"
    r"|实名|验证|认证|报名|邀请|试用金|权益")

# 纯折扣/无免费额度的条目 → 不是「可领的权益」，单独列
NO_REAL_BENEFIT = re.compile(
    r"^\s*$|限时折扣|首发五折|首发|促销价|每百万token|输入\$|输出\$|每秒低至|低至\d")

# 跨帧接续卡片的平台名补全（通用解析器解不了，这里人工兜底）。
# 证据：这 4 条是标题在上一帧底部、权益在本帧顶部的长卡片，
# 平台名可在相邻帧的同一位置读到（帧51/52 的 Google、帧54 的 OpenAI / Kiro）。
TITLE_FIX = {
    "阿拉伯地区18岁以上在校大学": "Google",
    "1万名科学家可申请": "Google",
    "美国认证医护（MD/DO、": "OpenAI",
    "早期到 A轮初创公司可申请最": "Kiro",
}

# 平台名归一：OCR 错字 + 大小写/空格差异 → 标准平台名。
# ⚠️ 每条都对着原始 OCR 版面核实过，不凭猜测映射。
PLAT_FIX = {
    # 两级职责边界（2026-11-13 明确）：
    #   第一级 `~/Documents/py/抖音视频文案提取.py` 的 FIX —— 修正**OCR 字面错字**
    #     （GrOg→Grok、Tavily Al→Tavily、Browse Al→Browse AI、Workato→Workato Free 等，
    #      2026-11-13 已补进一级）。
    #   第二级 PLAT_FIX（本表）—— 修正**第一级之后仍然错位**的部分：
    #     ① 标题被 OCR 折行切断（"上海人工智能实验室（书生・" + "端砚）"）
    #     ② 平台名被整行权益挤掉，权益行被误当平台名（"在校学生100 美元额度，"→Microsoft）
    #     ③ 平台名被权益后缀污染（"阿里 Qoder CN"→阿里 Qoder）
    #   ⇒ 与一级重复的条目**故意保留**做防御：一级 FIX 若被后续改动破坏，这里还能兜住。
    #   ⚠ 本表键必须能在**真实 OCR 原文**或**一级输出**里命中，否则是死条目。
    #     （2026-11-13 已修：TavilyAl→Tavily Al，原键拼错导致永不命中）
    # --- OCR 同音/形近错字（主脚本 FIX 覆盖不到的整词错字）---
    "GrOg": "Grok",            # xAI，帧41 列4 标题行（w=0.0418 窄行）
    "百度飞楽 AI Studio": "百度飞桨 AI Studio",   # 帧34 列2 标题行
    "飞桨 EasyDL 专业版脚本调参": "百度飞桨 AI Studio",  # 同上一张卡的权益被切成了标题
    "上海人工智能实验室（书生・": "上海人工智能实验室（书生·端砚）",  # 标题被折行切断
    "端砚）": "上海人工智能实验室（书生·端砚）",
    "智增 ZCode": "智谱 ZCode",   # 帧38 列3
    "智谱 ZCode": "智谱 ZCode",
    "腾云 TokenHub": "腾讯云 TokenHub",   # 帧34 列0 标题行
    "商阂小浣熊": "商汤小浣熊",
    "商沕小浣熊": "商汤小浣熊",
    # --- 标题被权益行挤掉后，权益行被误当平台名 → 归到真实平台 ---
    "存在免费档，限速规则见官方": "Grok",   # 帧41 列4：Grok 的描述行
    "学生价＄5.99/月订阅": "Mistral",       # 帧41 列0：Mistral 学生价
    "Pro档（20美元/月）可免费": "Devin",   # 帧41 列0：Devin Pro 档
    "在校学生100 美元额度，": "Microsoft",   # 帧41 列2：Microsoft 学生额度
    "新用户文本模型约100万": "阿里云百炼",   # 帧31 列3：阿里云百炼 新用户额度
    "阿里云 Token Plan 低至": "阿里云 Token Plan",
    "商汤日日新 Token Plan公测": "商汤日日新",
    "商汤日日新 Token Plan 公测": "商汤日日新",
    "扣子升级订阅体系后，个人": "扣子 Coze",
    "天翼云星辰50万 Tokens": "天翼云星辰",
    "ZCode 邀请好友活动10-12至": "智谱 ZCode",
    "ZCode 邀请好友活动10-12 至": "智谱 ZCode",
    "100美元体验金": "Novita AI",           # 帧2 封面卡：Novita 100 美元体验金
    "WorkBuddy Hy4": "WorkBuddy",
    "fal FLUX3 Max 11-21前": "fal",
    "飞桨 EasyDL 专业版脚本调参 ": "百度飞桨 AI Studio",
    "Browse Al": "Browse AI",
    "Tavily Al": "Tavily",
    "AppAIGateway": "App AI Gateway",
    "BrowseAI": "Browse AI",
    "Workato": "Workato Free",
    "KirO": "Kiro",
    "腾讯云 TokenHub": "腾讯云 TokenHub",
    "Google Gemini API": "Google Gemini API",
    "Google开发者计划": "Google 开发者计划",
    "百度飞桨 AI Studio": "百度飞桨 AI Studio",
    "飞桨 EasyDL 专业版脚本调参": "百度飞桨 AI Studio",
    "上海人工智能实验室（书生・端砚）": "上海人工智能实验室（书生·端砚）",
    "今天值得拿": "Novita AI",
    "今天值得拿的": "Novita AI",
    "阿里 Qoder CN": "阿里 Qoder",
    "百度秒哒": "百度秒哒",
    "智谱 AutoClaw": "智谱 AutoClaw",
    "VideoGen": "VideoGen",
    "数眼智能": "数眼智能",
}


def plat_key(p):
    """平台名归一化 key：去空格/连字符 + 小写。用于 SITE 表查找。"""
    return re.sub(r"[\s\-_]+", "", p).lower()


def _check_dup_keys():
    """自检：SITE / PLAT_FIX 表里有重复 key 时直接报错。
    重复 key 会被 Python 静默覆盖（后者胜），导致链接莫名失效或错配——
    实测踩过 2 次（comfycloud/grok/codegpt、openai/orshot 各被写了两遍）。
    """
    import collections
    for name, tbl in (("SITE", SITE), ("PLAT_FIX", PLAT_FIX), ("ALT_CHANNEL", ALT_CHANNEL)):
        # 只看字面 key（PLAT_FIX 允许「同 key 多映射」？不允许，一样报错）
        dups = [k for k, v in collections.Counter(tbl.keys()).items() if v > 1]
        if dups:
            raise SystemExit("[待领清单] %s 表存在重复 key：%s\n"
                             "  → 重复 key 会被静默覆盖，必须删掉旧的。"
                             % (name, dups))


def lookup_site(*names):
    """按归一化 key 查 SITE 表；查不到再退化为「搜平台名」。"""
    for n in names:
        if not n:
            continue
        v = SITE.get(plat_key(n))
        if v:
            return v
        # 后缀匹配：平台名后面跟了权益片段（如 "XCode 邀请好友活动"）
        for k, v in SITE.items():
            if v and plat_key(n).startswith(k) and len(k) >= 4:
                return v
    return ""


# 平台 → 官网/领取入口提示
# ⚠️ 全部条目已于 2026-11-13 用 curl 实测过 HTTP 状态码（非 2xx/3xx 的已剔除或修正）。
# ⚠️ key 统一用「小写 + 去空格/连字符」形式，查找时用 plat_key() 归一化，
#    否则 Gravex Chat / Gravex chat 这类大小写变体会漏查（实测踩过）。
SITE = {
    # ---- 国内 ----
    "七牛云": "https://portal.qiniu.com/",
    "智谱bigmodel": "https://bigmodel.cn/",
    "阿里云权益中心": "https://www.aliyun.com/benefit",
    "腾讯云": "https://cloud.tencent.com/",
    "腾讯云开发cloudbase": "https://cloud.tencent.com/",
    "腾讯云tokenhub": "https://cloud.tencent.com/product/tokenhub",
    "腾讯混元大模型": "https://cloud.tencent.com/product/hunyuan",
    "腾讯混元": "https://cloud.tencent.com/product/hunyuan",
    "阿里云百炼": "https://bailian.console.aliyun.com/",
    "阿里云tokenplan": "https://www.aliyun.com/benefit",
    "阿里云云工开物": "https://www.aliyun.com/benefit",
    "阿里qoder": "https://qoder.com/",
    "秒悟meoo": "https://www.aliyun.com/benefit/scene/meoo",
    "超集互联网scnetmaas": "https://www.aliyun.com/benefit",
    "科大讯飞星火": "https://xinghuo.xfyun.cn/",
    "商汤小浣熊": "https://chat.sensetime.com/",
    "商汤日日新": "https://platform.sensenova.cn/",
    "火山引擎方舟": "https://console.volcengine.com/ark",
    "硅基流动": "https://cloud.siliconflow.cn/",
    "百度飞桨aistudio": "https://aistudio.baidu.com/",
    "百度智能云千帆": "https://cloud.baidu.com/product/qianfan",
    "百度千帆": "https://cloud.baidu.com/product/qianfan",
    "白度千帆": "https://cloud.baidu.com/product/qianfan",
    "microsoft": "https://www.microsoft.com/",
    "azure": "https://azure.microsoft.com/",
    "kimiapi开放平台": "https://platform.moonshot.cn/",
    "华为云码道": "https://www.huaweicloud.com/",
    "华为云码道codearts": "https://www.huaweicloud.com/",
    "trae": "https://www.trae.cn/",
    "扣子coze": "https://www.coze.cn/",
    "智谱zcode": "https://zcode.ai/",
    "zcode": "https://zcode.ai/",
    "秒哒": "https://www.miaoda.cn/",
    "石墨办公": "https://shimo.im/",
    "天翼云": "https://www.ctyun.cn/",
    "天翼云星辰": "https://www.ctyun.cn/",
    "阶跃星辰": "https://platform.stepfun.com/",
    "上海人工智能实验室书生端砚": "https://openi.pcl.ac.cn/",
    "数眼智能": "https://www.gsdata.cn/",
    "摩尔线程": "https://www.mthreads.com/",
    "moonshotkimi": "https://platform.moonshot.cn/",
    "minimaxmplan": "https://platform.minimaxi.com/",
    "minimax": "https://platform.minimaxi.com/",
    "workbuddy": "https://www.workbuddy.cn/",
    "libtv": "https://www.liblib.tv/",
    "liblibai": "https://www.liblib.tv/",
    # ---- 国外 ----
    "novitaai": "https://novita.ai/",
    "novitasandbox": "https://novita.ai/",
    "simularbox": "https://simular.ai/",
    "openrouter": "https://openrouter.ai/",
    "opencode": "https://opencode.ai/",
    "github": "https://github.com/",
    "cursor": "https://cursor.com/",
    "cline": "https://cline.bot/",
    "jetbrains": "https://www.jetbrains.com/",
    "cerebras": "https://cloud.cerebras.ai/",
    "firecrawl": "https://firecrawl.dev/",
    "cohere": "https://cohere.com/",
    "notionbusiness": "https://www.notion.so/",
    "notion": "https://www.notion.so/",
    "z.ai": "https://z.ai/",
    "anthropic": "https://console.anthropic.com/",
    "openai": "https://platform.openai.com/docs",
    "elevenlabs": "https://elevenlabs.io/",
    "nvidiabuild": "https://build.nvidia.com/",
    "fireworksai": "https://fireworks.ai/",
    "coderabbit": "https://coderabbit.ai/",
    "databricks": "https://www.databricks.com/",
    "aws": "https://aws.amazon.com/",
    "kiro": "https://kiro.dev/",
    "base44": "https://base44.com/",
    "deepgram": "https://deepgram.com/",
    "tavily": "https://tavily.com/",
    "google": "https://ai.google.dev/",
    "googlegeminiapi": "https://ai.google.dev/",
    "google开发者计划": "https://developers.google.com/",
    "google开发者": "https://developers.google.com/",
    "workatofree": "https://www.workato.com/",
    "fal": "https://fal.ai/",
    "pika": "https://pika.art/",
    "rime": "https://rime.ai/",
    "gravexchat": "https://gravex.ai/",
    "gravex": "https://gravex.ai/",
    "runway": "https://runwayml.com/",
    "supermaven": "https://supermaven.com/",
    "commandcode": "https://commandcode.ai/",
    "morphllm": "https://morphllm.com/",
    "localhero.ai": "https://localhero.ai/",
    "webscraping.ai": "https://webscraping.ai/",
    "browseai": "https://browse.ai/",
    "cloudflare": "https://developers.cloudflare.com/workers-ai/",
    "capcut": "https://www.capcut.com/newsroom/capcut-creative-campus",
    "百度秒哒": "https://www.miaoda.cn/",
    "workato": "https://www.workato.com/",
    "inference.net": "https://inference.net/",
    "lumenfall": "https://lumenfall.ai/",
    "yotta": "https://yotta.com/",
    "agnesai": "https://agnes.ai/",
    "tomoai": "https://tomo.ai/",
    "tomosuai": "https://tomosu.com/",
    "reportgpt": "https://reportgpt.com/",
    "tinyfish": "https://tinyfish.ai/",
    "affinitybycanva": "https://affinity.serif.com/",
    "plaud": "https://plaud.ai/",
    "powtoon": "https://www.powtoon.com/",
    # ⚠️ 三条特殊处理的链接（2026-11-13 实测）：
    #   comfycloud → www.comfy.cloud TLS 失败(exit 35)，改用 comfy.org/pricing
    #            （官方定价页原文含「5 free runs on real GPUs — no credit card required」）
    #   grok       → grok.com / x.ai 全部 403（Cloudflare 挡 bot），改用 docs.x.ai
    #   codegpt    → codegpt.io 502（服务端宕机），改用其 GitHub 组织页
    "comfycloud": "https://comfy.org/pricing/",
    "grok": "https://docs.x.ai/",
    "codegpt": "https://github.com/CoderGPT",
    # ⚠️ 这两个站用 HTTP/2 + br 协商时 curl 报 000（exit 35），
    #    换 --http1.1 + 完整 UA 就是 200 —— 是本机 curl 的 HTTP/2 协商问题，
    #    不是站点不可达。浏览器打开正常。
    "orshot": "https://orshot.com/",
    # ⚠️ platform.openai.com 直连 403（Cloudflare 挡 bot），换 /docs 路径仍 403，
    #    故指向官方 API 文档首页（可正常打开）。
    "openai": "https://platform.openai.com/docs",
    "workersai": "https://workers.cloudflare.com/",
    "appaigateway": "https://appaigateway.com/",
    "videogen": "https://videogen.io/model-1",
    "snill": "https://snill.ai/",
    "amd": "https://developer.amd.com/ai-dev-program",
    "amdradeoncloud": "https://developer.amd.com/ai-dev-program",
    "mistral": "https://console.mistral.ai/",
    "devin": "https://devin.ai/",
    "antigravity": "https://antigravity.google/",
    "智谱autoclaw": "https://bigmodel.cn/",
    "modellimit": "",
}


def classify(platform, benefit):
    """返回 (档位, [标签列表])"""
    text = platform + " " + benefit
    tags = []
    level = "L0"
    order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}
    # 先判断是否明确「免绑卡/免信用卡」——是则根本不该出现「绑卡」标签
    no_card = bool(FREE_NO_CARD.search(text))
    for lv, subs in RULES:
        for name, pat in subs:
            if name == "绑卡" and no_card:
                continue
            if re.search(pat, text):
                tags.append(name)
                if order[lv] > order[level]:
                    level = lv
    if no_card:
        tags.append("免绑卡")
        # 明确免绑卡 → 最高只到 L1（仍要注册）
        if level in ("L2", "L3", "L4"):
            level = "L1"
    # 去重保序
    tags = list(dict.fromkeys(tags))
    return level, tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("-o", "--out", default="待领清单.md")
    ap.add_argument("--json", dest="json_out", default="")
    a = ap.parse_args()

    _check_dup_keys()

    data = json.load(open(a.json_path, encoding="utf-8"))
    # 卡片总数取实际值，不要硬编码（上一版写死 146，重跑后已变 142）
    n_cards = len(data)

    # ---- 去重：同平台 + 权益归一化（去空白/省略号/标点差异）后只留一条
    def norm(s):
        return re.sub(r"[\s⋯…、，,。：:；;（）()【】\[\]「」\-–—·•]+", "", s)

    seen, uniq = set(), []
    for c in data:
        k = (norm(c["platform"]), norm(c["benefit"])[:30])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    # 同平台多权益的，再按平台归并（保留信息，用 | 并列）
    by_plat = defaultdict(list)
    for c in uniq:
        by_plat[c["platform"]].append(c)

    buckets = {"L0": [], "L1": [], "L2": [], "L3": [], "L4": []}
    skipped = []
    for plat, items in by_plat.items():
        benefits = " | ".join(dict.fromkeys(i["benefit"] for i in items))
        if not HAS_REAL_BENEFIT.search(benefits) and not NO_REAL_BENEFIT.search(benefits):
            skipped.append((plat, benefits))
            continue
        real_plat = TITLE_FIX.get(plat, plat)
        real_plat = PLAT_FIX.get(real_plat, real_plat)
        lv, tags = classify(real_plat, benefits)
        buckets[lv].append({
            "platform": real_plat, "benefit": benefits, "tags": tags,
            "frames": sorted(set(i["frame"] for i in items)),
            "site": lookup_site(real_plat, plat),
            "ocr_name": plat if real_plat != plat else "",
        })

    # 平台名补全后，同平台会聚到一起 → 再归并一次（保留全部权益）
    merged = {}
    for lv, items in buckets.items():
        for it in items:
            k = (lv, plat_key(it["platform"]))
            if k in merged:
                m = merged[k]
                if it["benefit"] not in m["benefit"]:
                    m["benefit"] = m["benefit"] + " | " + it["benefit"]
                m["tags"] = list(dict.fromkeys(m["tags"] + it["tags"]))
                m["frames"] = sorted(set(m["frames"] + it["frames"]))
                if not m["site"]:
                    m["site"] = it["site"]
                if not m.get("ocr_name") and it.get("ocr_name"):
                    m["ocr_name"] = it["ocr_name"]
            else:
                merged[k] = dict(it)
    buckets = {lv: [] for lv in buckets}
    for (lv, plat), it in merged.items():
        buckets[lv].append(it)

    names = {
        "L0": ("L0 · 免门槛（注册即得，不要卡不要验证）", "✅ 最低门槛，先领这些"),
        "L1": ("L1 · 需注册 / 需邀请 / 需填问卷", "需你本人注册账号，可能还要加群或填问卷"),
        "L2": ("L2 · 需实名认证（企业或个人）", "要营业执照或实名，周期通常 1-3 个工作日"),
        "L3": ("L3 · 需绑卡 / 支付方式", "⚠️ 绑卡后可能自动扣费，务必看清是否需要先充值"),
        "L4": ("L4 · 需身份资质验证（学生/教师/医护/军人等）", "需本人资质证明，别人代不了。**注意：多数条目还有免学生身份的替代通道**，见本段末尾"),
    }
    # L3 逐条的扣费风险提示（不触发任何绑卡/支付动作，只做提醒）
    L3_WARN = {
        "Cerebras": "绑卡后若不免费即按量计费；建议领完额度后立刻在控制台删除支付方式，或用虚拟卡。",
    }
    total = sum(len(v) for v in buckets.values())

    lines = ["# 待领清单 · 短视频画面清单 11-12（%d 条）\n" % total,
             "> 来源：11-12 视频画面 OCR（%d 条原始卡片 → 去重 %d 条平台 → 筛出 %d 条可行动）\n"
             % (n_cards, len(by_plat), total),
             "> **本清单由脚本生成，只做信息整理与门槛分级，不代你注册、不扫码、不碰你的支付方式。**\n",
             "> 入口链接已于 2026-11-13 逐条用 curl 实测可达；标「搜平台名」的是小平台，请搜平台全名。\n",
             "> L2/L3/L4 各段末尾附「替代通道」——多数「需身份」的条目其实有**不用学生身份**也能走的路。\n",
             "## 怎么用\n",
             "1. 先扫 **L0**（不要卡不要验证，纯注册）→ 十几分钟能领完大半；",
             "2. 再看 **L1**，挑你本来就会用的平台（别为领优惠装一堆没用的 App）；",
             "3. **L2/L3/L4** 只在你真的需要该产品时再办——实名要时间，绑卡有扣费风险，身份验证只有本人能过。",
             "4. 领完在下面勾选 `☐`。\n"]
    for lv in ("L0", "L1", "L2", "L3", "L4"):
        items = buckets[lv]
        title, tip = names[lv]
        lines += ["\n## %s（%d 条）\n" % (title, len(items)), "> %s\n" % tip]
        if not items:
            lines.append("（无）\n")
            continue
        lines.append("| ☐ | 平台 | 权益 | 门槛标签 | 入口 |")
        lines.append("|---|---|---|---|---|")
        for it in sorted(items, key=lambda x: x["platform"]):
            site = it["site"] or "搜平台名"
            lines.append("| ☐ | %s | %s | %s | %s |" % (
                it["platform"], it["benefit"][:80].replace("|", "／"),
                "、".join(it["tags"]) or "无", site))
        if lv == "L3":
            for it in items:
                warn = L3_WARN.get(it["platform"])
                if warn:
                    lines.append("")
                    lines.append("- ⚠️ **%s**：%s" % (it["platform"], warn))
            lines.append("")
        # L2/L3/L4 附「替代通道」——很多「需身份」的条目其实有不用学生身份的路
        alt_items = [it for it in sorted(items, key=lambda x: x["platform"])
                     if it["platform"] in ALT_CHANNEL]
        if alt_items:
            lines.append("")
            lines.append("### 替代通道（不用学生身份也能走的路）\n")
            lines.append("> 依据 2026-11-13 各平台官方页面实证；完整版见同目录 `身份通道决策表.md`\n")
            for it in alt_items:
                lines.append("- **%s**：%s" % (it["platform"], ALT_CHANNEL[it["platform"]]))
            lines.append("")

    if skipped:
        lines += ["\n## 已排除（无免费额度，纯折扣/定价信息，共 %d 条）\n" % len(skipped)]
        for p, b in skipped:
            lines.append("- %s — %s" % (p, b[:70]))

    open(a.out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("[待领清单] 共 %d 条 → %s" % (total, a.out))
    for lv in ("L0", "L1", "L2", "L3", "L4"):
        print("   %s: %d 条" % (lv, len(buckets[lv])))
    if skipped:
        print("   已排除（无免费额度）: %d 条" % len(skipped))
    if a.json_out:
        json.dump({"buckets": buckets, "skipped": skipped},
                  open(a.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("   JSON → %s" % a.json_out)


if __name__ == "__main__":
    main()
