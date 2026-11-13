---
name: douyin-video-transcribe
description: 提取短视频里的文案/画面文字/清单卡内容，产出「文字稿.md」与结构化「清单.md」。当用户说"提取这个视频的文案""这个视频讲了什么""把视频里的文字扒出来""视频转文字""视频里的优惠清单"时使用。
agent_created: true
---

# 抖音视频文案提取（画面 OCR + 卡片还原）

把抖音视频里的**画面文字**和**口播文字**提取成可读文字稿；当视频是「清单卡」版式时，
再还原成「标题 | 内容」的结构化表格。

**直接复用成品，不要从零重写**（链路已跑通并实测，见下方实数）。

## 成品位置（已部署，2026-11-13 实测）

| 项 | 路径 |
|---|---|
| **主脚本（自包含单文件）** | `~/Documents/py/抖音视频文案提取.py` |
| OCR 工具（自动编译缓存） | `~/.workbuddy/cache/douyin-transcribe/swiftocr` |
| 登录态（复用抖音收藏那套） | `~/Developer/douyin-favorites/.browser_profile/` |
| 产物目录 | `~/Developer/douyin-favorites/transcripts/<aweme_id>/` |
| ASR 环境（可选，需先装模型） | `~/Developer/douyin-favorites/.venv-asr/` |

主脚本**自包含**：内嵌 Swift OCR 源码（首次自动编译）、卡片还原规则、下载层、错字映射表。
拷到任何 Mac 都能跑，只依赖系统自带 + 已装的 patchright / ffmpeg。

## 跑法

```bash
# ① 抖音 URL（走浏览器截获 → 下载 → 抽帧 → OCR → 清单）
python3 ~/Documents/py/抖音视频文案提取.py "<抖音视频URL>"

# ② 本地视频（不联网）
python3 ~/Documents/py/抖音视频文案提取.py --local ~/path/to.mp4

# ③ 口播 ASR（需先装 mlx-whisper + 下模型，见下方「ASR 的现实约束」）
python3 ~/Documents/py/抖音视频文案提取.py "<URL>" --mode both --model mlx-community/whisper-base-mlx

# ④ 离线自检（16 项断言，改完代码必跑）
python3 ~/Documents/py/抖音视频文案提取.py --selftest
```

常用参数：`--out DIR`（产物目录）、`--eps 1.5`（抽帧间隔秒）、`--keep`（保留视频与帧）、
`--headless`（无头浏览器）、`--mode ocr|asr|both`。

**依赖**：
- patchright 必须用 `~/Developer/social-auto-upload/.venv/bin/python`（managed 的 3.13 里没有 patchright）；
- ffmpeg/ffprobe：`/opt/homebrew/bin`；
- swiftc：系统自带（`xcrun` 链）。

## 产物

| 文件 | 内容 |
|---|---|
| `文字稿.md` | 画面文字（按帧去重，保留 `[帧N]` 出处）+ 口播文字 |
| `画面文字.tsv` | OCR 原始结果：`frame / x / y / w / h / text`（归一化坐标） |
| `清单.md` / `清单.json` | 卡片版式还原出的「标题 | 内容 | 帧」结构表 |
| `summary` JSON | stdout 最后一行：`{"aid","work","ocr_rows","asr_chars","cards"}` |

## 优惠清单 → 待领清单（两级加工）

`清单.json` 是**信息列表**，不能直接行动。第二步用筛选器转成**可执行清单**：

```bash
python3 ~/Developer/douyin-favorites/tools/生成待领清单.py \
        <清单.json> -o 待领清单.md --json 待领清单.json
```

门槛分档（从低到高）：

| 档 | 含义 | 实测条数（11-12 视频） |
|---|---|---|
| **L0** | 免门槛：注册即得，不要卡不要验证 | 58 |
| **L1** | 需注册 / 需邀请 / 需填问卷 | 16 |
| **L2** | 需实名认证（企业或个人） | 2（百度千帆 ×2 条） |
| **L3** | 需绑卡 / 支付方式 ⚠️ 可能自动扣费 | 1（Cerebras，附扣费提示） |
| **L4** | 需身份资质（学生/教师/医护/军人/初创） | 9（GitHub/Microsoft/OpenAI/JetBrains/Kiro/Base44/CapCut/阿里云云工开物/Google） |

排除规则：纯折扣/定价信息（无免费额度）不计入，共排除 20 条。
合计 **86 条**（原 87 → 86 是**修复带来的去重正确化**：`WorkBuddy Hy4` 修正为 `WorkBuddy` 后，
它与另一张 `WorkBuddy Challenge` 卡被正确合并为 1 条，两条权益用 `|` 并列保留，未丢信息）。

**入口链接**：`SITE` 表 107 条平台**全部有链接**（0 条无链接），2026-11-13 用 curl 逐条实测：
76/78 返 200，剩 2 条（kiro.dev / tavily.com）返 403 但响应体是**完整正常 HTML**
→ Cloudflare 的 bot 防护加了 403 状态码，浏览器打开正常，链接不用改。

⚠️ `SITE` / `PLAT_FIX` 表**不允许出现重复 key**（Python 会静默覆盖，导致链接莫名失效）。
脚本启动时 `_check_dup_keys()` 会直接报错拦下。

## 🔴 实测判据（都是量出来的，不是猜的）

某科技资讯视频实测：**60 帧 → 1761 文本框 → 146 条卡片**。

| 项 | 判据 | 依据 |
|---|---|---|
| 标题 vs 描述 | **不能用字高**（实测连续无断层）→ 用**宽度**：标题 w≤0.11，描述 w 0.15-0.19 | 同一帧字号分布实测 |
| **标题兜底必须是窄行** | 判据是 `len(t)<=20 **and w<0.125**`，不是光看长度 | 实测帧41 列0：Mistral 标题 w=0.0578，下一行「学生价＄5.99/月订阅」w=0.1347 是**描述**，旧兜底把它当标题 → Mistral 整条丢失 |
| 描述句特征 | 含「：」或以「…/⋯」结尾 | 卡片文本被截断 |
| 标题最强信号 | **下一行含本行特征词**（TRAE → "TRAE 注册即送2,500积分"） | 卡片结构 |
| 分列依据 | **左边缘**聚类（间隙实测 0.16+）；**不能用中心点**（会被宽描述行带偏） | 帧 31 左边缘实测 0.072/0.295/0.520/0.744 |
| 标题被 OCR 断行 | 同列内「（」未闭合 + 下一行含「）」且不含「（」→ 合并 | 实测帧34 列1「上海人工智能实验室（书生・端砚）」被切成宽行+窄行两框 |
| **错字映射分两级** | 一级 `FIX` 修**OCR 字面错字**；二级 `PLAT_FIX` 只修**一级之后的错位**（标题折行 / 平台名被权益挤掉 / 被后缀污染） | 错字只补二级 = 假修：一级产物里就已经错了 |
| **映射键必须与 OCR 原文逐字一致（含空格）** | 每个新键回 `ocr.tsv` grep 一次原文 | 我把 `Tavily Al`（原文**有空格**）写成 `TavilyAl` → 永不命中，跑出来还是 `Tavily Al`；`BrowseAI`/`AppAIGateway` 原文根本不存在 → 死条目 |
| **键是值的子串时用正则负向断言** | `FIX_RE` 表**先于** `FIX` 执行，如 `r"Workato(?! Free)"` | `str.replace` 会把下一行原文「Workato Free 注册送」改成「Workato Free Free 注册送」 |
| 判「某键可从二级删除」 | 用 `一级.fix_text(键)==目标值`，**不能**用「键出现在 OCR 原文」（键常是一级产物） | 我误判冗余 0 条，实际 18 条 |
| 改过 `fix_text` 后如何验收 | diff **平台名集合**，不能只比条数 | 条数不变也可能全是改名；本轮 142 条不变、5 处就地改名 |
| 产物头部数字 | 必须动态生成（`len(data)`），**不许硬编码** | 上轮写死「146 条原始卡片」，重跑后实际 142 |
| 核验脚本取字段 | 先 `print(list(obj.keys()))` + 看顶层类型 | 用 `i.get('url')` 统计链接，而真实字段叫 `site` → 误报「87 条无链接」 |

## 🔴 必须记住的坑（都真踩过）

1. **`media-audio` 冒充视频**：抖音同一条作品给画面和纯音轨**两个 rendition**，
   取错只下到音频 → ffmpeg 报 `Output file does not contain any stream`。
   必须 `ffprobe -select_streams v:0` 复核（本脚本已内建该校验）。
2. **yt-dlp / curl 拿 web detail JSON 一律 403**（要 X-Bogus 签名），此路已废；
   但**页面自己**会带签名调接口 → 在浏览器里截获响应即可，不必自己算签名。
3. **下载要用浏览器自身的 request context**（自动带登录 cookie），不要手工拼 header。
4. **同音错字**：Vision OCR 会出「沙稍→沙箱、干帆/白度干帆→千帆、腾云→腾讯云、飞奖→飞桨、
   智增→智谱、商阂→商汤、Gline→Cline、Comty Gloud→Comfy Cloud、OpenAl→OpenAI」。
   映射表在主脚本的 `FIX` 字典，**发现新错字就往里加**（改前先 WebSearch 实证，别瞎猜）。
5. **平台名被权益污染**：OCR 常把标题和首行权益识别成一个框（「Workato Free 注册送」）。
   主脚本的 `SPLIT_RULES` 按真实输出逐条校准，命中就切回 (平台名, 权益)。
6. **假绿陷阱**：抽帧有图但 OCR 0 行、或抽帧 0 帧，脚本会**抛错退出**（不写空产物），
   避免下游以为"跑通了"。
7. **nohup + `&` 起的后台进程随父 shell 退出被杀** → 用后台任务机制起，别 nohup &。
8. **🔴 跨帧卡片解不了，别再试**：少数卡片是跨帧接续的长卡片（标题在上一帧底部、
   权益在本帧顶部）。试过 ①单帧内向上找宽行 ②相邻帧同列配对 —— **都会跨卡片误归属**
   （把 Snill 标题配到 OpenRouter 的权益、把 CodeGPT 配到 Google，145 条会变成 168 条
   且 22 条被错配）。**宁可少 4 条，不要错配 20 条**。这 4 条在
   `生成待领清单.py` 的 `TITLE_FIX` 表里人工兜底（平台名可在相邻帧读到）。
9. **封面页/结尾页不是卡片网格**：帧 1/2 是整页排版（「今天值得看」「条重点」等），
   帧 6/25 是重点推荐页（超宽段落 w=0.53）。别把它们当卡片解析——
   已在 `NOISE` 里排掉封面词。
10. **curl 测链接的 3 个坑**（2026-11-13 实测）：
   - `www.comfy.cloud` / `novita.ai` / `orshot.com` 用默认 HTTP/2 报 `000`（exit 35
     TLS 协商失败），加 `--http1.1` + 完整 UA 头就是 200 → **是本机 curl 的问题，
     不是站点不可达**。
   - `grok.com` / `x.ai` / `platform.openai.com` / `kiro.dev` / `tavily.com` 返 403，
     但**响应体是完整正常 HTML** → Cloudflare bot 防护加的状态码，浏览器正常。
   - 判定「站点真不行」要看**响应体**（KB 铁律：curl 返回 403 ≠ 浏览器打不开）。
11. **SITE 表重复 key 会静默覆盖**（Python dict 语义），实测踩过 2 次导致链接莫名失效
    → 生成器启动时 `_check_dup_keys()` 直接报错拦下。
12. **🔴 错字只补第二级 = 假修**（2026-11-13 第三轮）：一级 `FIX` 修 OCR 字面错字，
    二级 `PLAT_FIX` 修一级之后的错位。此前只补二级 → 错字在**一级产物里就已经错了**。
    ⇒ 错字一律回 `ocr.tsv` 查原文**先修一级**。
13. **映射键必须与 OCR 原文逐字一致（含空格）**：我把 `Tavily Al` 写成 `TavilyAl` →
    永不命中，跑出来还是 `Tavily Al`；`BrowseAI`/`AppAIGateway` 原文根本不存在 → 死条目。
    ⇒ 每个新键回 `ocr.tsv` grep 一次原文。
14. **`str.replace` 子串替换会二次替换**：`"Workato"→"Workato Free"` 会把下一行原文
    「Workato Free 注册送」改成「Workato Free Free 注册送」→ 新增 `FIX_RE` 正则表**先于**
    `FIX` 执行，用负向断言 `r"Workato(?! Free)"`。扫 42 条键确认**只有 Workato 一条真炸**。
15. **🔴 L4「需身份」≠ 必须学生身份**（2026-11-13 实证）：9 条 L4 里只有 3 条真是学生专属，
    5 条有别的入口（免身份免费层 / 教师通道 / 开源维护者通道）。
    ⇒ 生成器内置 `ALT_CHANNEL` 表，L2/L3/L4 各段自动输出「替代通道」小节。
    ⇒ **教师通道额度常高于学生通道**（GitHub 教师给 Copilot Pro 可手选模型，学生给 Student 只能 Auto）。
    ⇒ **JetBrains 是认证枢纽**：支持 `Authorize with GitHub`，有 GitHub Student Pack 就跟着过。
    ⇒ **额度最高的两条不需要学生**：Claude for OSS（Max 20x×6 月）+ GitHub Copilot 维护者（Pro），
      硬条件是「GitHub 账号 ≥2 年 + 近 90 天**公开** OSS 贡献」——⚠️ **私有仓库不算**。
    ⇒ 查政策别只搜「学生优惠」，要一并搜 teacher/educator/maintainer/non-commercial。
16. **教育类政策有时效，别照抄旧攻略**（2026 现行实测）：
    GitHub Copilot Student 04-20 暂停新签 → **06-16 起逐步重开**；
    Azure for Students **每人终身仅一次**且激活即计时；
    ChatGPT 教师版**限美国 K-12**、明确不适用学生；
    CapCut Creative Campus 仅 5 国 + 指定合作高校；Google AI Pro 学生版**需绑卡**、到期 $19.99/月。
    ⇒ Gemini 的三种「免费」互不相通：AI Studio 网页版 Free / Gemini API Free Tier /
    Google AI Pro 学生订阅（**订阅权益只限 AI Studio 网页界面，API Key 单独计费**）。

## ASR 的现实约束（如实说）

口播轨代码已就绪，但**本机实测 HF 下载带宽只有 20-30KB/s**（hf-mirror 18、官方直连 24、
官方走代理 30），whisper-base（290MB）要 2-3 小时；且本机磁盘仅剩约 7GiB。
所以：**清单类视频的价值在画面不在口播**，默认 `--mode ocr`；要跑 ASR 请显式指定并提前下好模型。

`mlx-community` 没有 `whisper-small` 档（写错名直接 404），可用：
`whisper-base-mlx` / `whisper-medium-mlx` / `whisper-large-v3-turbo`。
本机另有「本地语音输入」App（Qwen3-ASR），但其模型**从未落进 `~/.cache/huggingface`**，
要用得下 3.4G —— 不划算。

## 安全边界

只做**只读下载 + 本地识别**。不替用户注册账号、不扫码授权抖音、不交出验证码
（平台 2026-10 已通报过「扫码领福利」类盗号黑产）。
