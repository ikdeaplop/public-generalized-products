---
name: kb-evolve-meta
description: kb-evolve 的"证明效果"元层 + 3.0 技能生命周期工具链。覆盖：进化账本（基线→改动→度量→留/退 + 进化分）、生效率统计、每周《进化效果周报》、从机 leaf 联邦，以及 3.0 三件套 Voyager（避坑→带四要素的技能）/ SAGE（遗忘曲线，治只增不减）/ SkillX（三机共享技能）。当用户要让 kb-evolve 的进化"可度量、可审计"，或要让知识库"长出可复用技能、有进有出、跨机共享"时使用。
agent_created: true
---

# kb-evolve 元层 + 3.0：证明效果 & 技能生命周期

让 kb-evolve 的进化从"跑完就算"变成"有物证、可度量、可周报"；
3.0 起再让知识库**长出技能（Voyager）、学会遗忘（SAGE）、跨机共享（SkillX）**。

> 完整架构见 `~/your-vault/资料库/01-项目与开发/kb-evolve-3.0-架构设计.md`

## 文件位置

| 用途 | 路径 |
|---|---|
| 元层 CLI | `~/.workbuddy/bin/kb-evolve/evolve_meta.sh`（record/effectiveness/weekly/score/federation） |
| 进化账本 | `~/.workbuddy/state/kb-evolve/evolution-ledger.jsonl`（append-only） |
| 事件驱动监听器 | `~/.workbuddy/bin/kb-evolve/kb-evolve-watch.sh`（FIFO，30s 节流） |
| 预算控制 | `~/.workbuddy/bin/kb-evolve/evolve_budget.sh`（时间/token/动作三帽） |
| ACE Reflector | `~/.workbuddy/bin/kb-evolve/reflector.py` |
| ACE Curator | `~/.workbuddy/bin/kb-evolve/curator.py` |
| **Voyager 技能库** | `~/.workbuddy/bin/kb-evolve/voyager.py` → `state/kb-evolve/skill-library.jsonl` |
| **SAGE 遗忘曲线** | `~/.workbuddy/bin/kb-evolve/sage.py` → 冷存 `skill-library-archive.jsonl` |
| **SkillX 联邦** | `~/.workbuddy/bin/kb-evolve/skillx.py` → `_共享/_<机>/技能/` + `skill-library-federation.jsonl` |
| 技能库可读渲染 | `~/your-vault/brain/state/voyager-skills.md`（已被 kb 索引，可直接检索） |
| 周报落盘 | `~/your-vault/_运维/周报/kb-evolve进化效果周报-年-Www.md` |
| 从机 leaf 包 | `~/your-vault/资料库/07-协作与规范/技能源码/kb-evolve-leaf/` |
| 调度 | `com.scg.kb-evolve-weekly`（launchd，周一 08:00） |

## 用法

```bash
# ---- 元层（2.0 沿用）----
evolve_meta.sh record --area "Voyager" \
  --baseline "经验散落成描写，没人照着做" \
  --change "避坑→技能，四要素 触发/做法/频次/成功率" \
  --metric "候选123→成技能100，hot79/warm21" \
  --verdict keep --compiled "voyager.py"
evolve_meta.sh effectiveness --since 2026-09-30
evolve_meta.sh weekly --week 2026-W40
evolve_meta.sh score
evolve_meta.sh federation

# ---- 3.0 三件套 ----
voyager.py --promote [--date X] [--dry-run]   # 避坑/经验 → 技能
voyager.py --render                            # 渲染 brain/state/voyager-skills.md
sage.py    --decay [--date X] [--dry-run]      # 遗忘衰减 hot→warm→cold→archived
skillx.py  --export | --import | --sync        # 三机共享技能分区
```

三者已接入 `run-evolve.sh` 第 **4.6** 步（Voyager→SAGE→SkillX），均戴预算帽且**容错不阻断**主链路。

## 关键坑（已踩过，已固化为代码）

1. **macOS bash 3.2**：`$var` 后紧跟全角字符吞进变量名 → unbound variable。写 `${x}）`。
2. **fswatch `>>文件` + `mv` 消费会静默丢事件**：首事件后 fd 指旧 inode。监听器必须用 **FIFO**（`watch.fifo`）。
3. **Voyager 频次失真**（2026-09-30）：交叉引用若只要求"标题**任 1 个** token 命中别的文件"，
   频次会被通用词撑到三位数（实测 131）、全库误判 hot。
   正解：**≥2 个 token 同时命中** + **语料级文档频率过滤**（DF > 25% 文件数的词剔除）。
   修后 max 131→68、中位 5，排序恢复合理。
4. **做法段抓到配置原文**：`voyager.py` 的 `section_body` 要跳过代码块 / XML-plist / 表格行，
   否则 plist 的 `<key>/<string>` 会被当成"做法"。
5. **`_共享/` 会被 structure_guard 拦截**：新建的三机共享分区必须先加白名单，否则搬进回收站（速查表已载）。
6. **🔴 技能库"存了没用"= 等于没有**（承《知识库沉淀与复用速度实测》）：
   该文档从 666 个会话统计出**真实触发率仅约 11%** —— 瓶颈从来不是写入速度（写入 0 秒、检索 0.06–0.28s），
   而是"有没有会话真的去查库"。软性"信息型"措辞的技能触发率极低。
   → **所以建了 Voyager 之后，必须同时给它硬触发入口**：把技能库查询挂进门 3（kb-pitfall-guard），
   用 ALWAYS TRIGGER 式措辞，别让它变成第二个"写进库却没人查的库"。
   这正是 `frequency` 至今没有真实触发回填的根因（见下）。

## 已知局限（诚实，勿谎称已完成）

- `frequency` 是**词法代理指标**（被多少篇笔记共同提到），**不是真实触发计数**。
  ✅ **真实触发计数已于 2026-11-04 接通**：新增「技能策展 SkillCurator」三件套
  （`~/.workbuddy/bin/skill-curator/`），用 **PostToolUse hook 捕获真实的技能调用**
  （`skill_usage.py` → `~/.workbuddy/state/skill-curator/usage.jsonl`），
  策展器据此按**真实闲置天数**分级而非 mtime 估算。
  ⚠️ 遥测 2026-11-04 才起，样本量小时判据仍偏保守 —— 详见技能卡 skill-curator。
  （本条原有的「尚未接通」已于 2026-11-04 修正为「已接通 + 样本量限制」。）
- `success_rate` 证据来源**只有 trigger-log 精确命中一条路径**（E058 删了 reflux.py 的 gate3 假证据块、
  E059 删了 record_trigger.py 的 `--auto-hard-trigger` 写入器，两端已拆干净）。
  当前 149 个技能中 **57 个有 sr 值**（全部 `_success_rate_src='real-use-trigger-log'`），
  其余 92 个为 `null`（诚实，进化分不加分）。
  ⚠️ 仍缺"修复后复检"闭环 —— sr 目前只有 0/1 两值，没有「调用后是否真的修好了」的二次验证。
- SkillX 当前**单机**：MBA/Win 不可达（`ssh mba` No route / winrm no-route），
  `_共享/_Win|MBA/技能/` 为空 → 跨机信号 0。需对端各跑一次 leaf bootstrap。
  `federation` 子命令返回空即**如实显示"暂无从机命名空间抵达"**，不要谎称已部署。

## ACE 三角色闭环（2026-09-30 接入）
Generator 一直有，但教训没被编译进规则（生效率长期 0）→ 补齐后两个角色：

| 角色 | 脚本 | 职责 |
|---|---|---|
| ① Generator | `run-evolve.sh` 引擎 / `kb_evolve_api.py` | 干活 → 产出黑匣子 |
| ② Reflector | `~/.workbuddy/bin/kb-evolve/reflector.py` | 从黑匣子提取经验：1 成功 + 1 失败，格式【当 X 时 → 应做 Y → 因为 Z】，追加进黑匣子 `## 七、经验条目` 段 |
| ③ Curator | `~/.workbuddy/bin/kb-evolve/curator.py` | 增量合并进 `daily_prompt.md` 的 `## 经验沉淀库` 段：去重跳过、失效路径标 ⚠️过时（不删）、改前备份 + manifest |

**只跑 Generator + Reflector、不开 Curator（要避免改 Playbook 时用）**
```bash
PY=${PYTHON}
EV=~/.workbuddy/bin/kb-evolve
# ⚠️ --out 必须用新文件名，否则会覆盖当日已有的 黑匣子-YYYY-MM-DD.md
$PY $EV/kb_evolve_api.py --date 2026-09-30 --scan ~/your-vault/brain/state/scan-latest.md \
    --out ~/your-vault/brain/outputs/黑匣子-2026-09-30-轮B-$(date +%H%M%S).md --limit 240
$PY $EV/reflector.py --box <上面的新黑匣子> --date 2026-09-30-B --limit 120 \
    --out-json ~/.workbuddy/state/kb-evolve/reflector-2026-09-30-B.json
```
不要直接跑 `run-evolve.sh` 来"只跑 Generator"：它会带跑 Curator 与 Voyager/SAGE/SkillX，
且 `BOX` 路径写死为当日黑匣子（会覆盖）；它开头还有幂等门禁（last-done == 今日则 SKIP）。

**ACE 两条硬约束（违反即退化为假进化）**
- 禁**简洁偏差**：不许把具体经验压成空话（"互链覆盖率仅 14.7%、1096 篇孤岛" ≠ "注意互链"）。
- 禁**上下文崩塌**：只做 delta（追加/编辑），**绝不整体重写** Playbook。验收用 `diff` 看是否只有 `Na M,N` 追加行。

**指标**：生效率 = 本轮新增进 Playbook 的条目数，见 curator JSON 的 `efficiency` 与 `curator-runs.jsonl`。

**已踩的坑（已固化进代码，勿回退）**
1. **假引擎复述污染黑匣子**：模型会把 SYSTEM 指令原样回显。`_valid_experience()` 必须判无效并回退 `deterministic_extract()`；写回黑匣子**只写解析出的干净【】条目**。
2. **按段标题截断会误删正文**：污染文本让 `## 七、经验条目` 出现在文件中间。截断前先确认标记出现次数与位置。
3. **`git checkout HEAD -- <file>` 恢复不了**：your-vault 有 auto backup 自动提交，HEAD 可能已是坏版本。必须 `git log` 挑更早 commit + 按字节数校验。
4. **🔴 Reflector 的回退路径必须单独跑测**：`trigger_of()` 只在 `curator.py` 有、`reflector.py` 从未定义，一直没暴露是因为模型输出总被前置判据拦下走不到那行；一旦放行就 `NameError` 整轮崩（2026-09-30 实测）。**假引擎常态化 ⇒ `deterministic-fallback` 才是真实主路径**。
5. **「一、」为空时成功经验会变"无"**：Generator 只出判断不出动作时已加 `_succ_from_behavior()` 回退到「四、我改了什么行为」。
6. **Curator 幂等性**：无改动时**不写盘**；只有 `merged>0` 才写/累加 manifest（空跑绝不覆盖首轮记录），另 append 一行到 `curator-runs.jsonl`。

## 真模型 Reflector 怎么跑通（2026-09-30 实测，第 7 次尝试才成功）

**必须用免费档路由名**：本网关（127.0.0.1:3001，283 个模型）**没有 LongCat / DashScope 命名（0 命中）**，
免费档只有 `free-router` 与 `auto:freefirst`。
```bash
$PY $EV/reflector.py --box <新黑匣子> --date <D> --limit 90 \
    --models "free-router,auto,auto:freefirst" --out-json ~/.workbuddy/state/kb-evolve/reflector-<D>.json
```

**🔴 非空 ≠ 有效（本轮最大坑，已修）**：`free-router` 有时会返回 `User Safety: safe`（3 个单词，非空）。
旧逻辑「只要非空就算模型成功」会直接 break，随后被 `_valid_experience()` 判无效 → 悄悄兜底。
现在必须**过有效性校验才算成功**，否则换下一个模型重试（轮C 连试 6 次失败，第 7 次才拿到真内容）。

**免费档实测（2026-09-30）**：`deepseek-v4-flash`/`glm-4.6`/`kimi-k2.5`/`deepseek-v3.2` 全 429；
`glm-4.5-air` 404；`qwen3-14b` 超时；`auto`/`auto:freefirst` 常复述 SYSTEM 指令
（`Thinking Process:` 或「首先，用户要求我作为 Reflector…」开头）→ 均判无效。
**网关是间歇性故障，重试是必需的，别一次失败就宣告模型路径不通。**

**心跳（防"安静地假装工作"）**：`~/.workbuddy/state/kb-evolve/reflector-heartbeat.json`
记录 `fallback_streak`；连续 ≥3 次兜底自动写告警到 `_共享源/_M4/避坑/`（阈值 `KB_REFLECTOR_FALLBACK_ALERT`）。
原始模型输出留证：`reflector-raw-<date>.txt` + JSON 的 `model_raw` 字段 —— **硬验收必须贴这个，不许拿兜底冒充**。

## 生效率口径（写死，后续照此复现）
- **ACE 生效率**必须拆两栏：**模型产出**（`engine=model` / `fallback=false`）vs **规则兜底**（`deterministic-fallback`）。
- **🔴 兜底那条不算真进化** —— 它是"流水线没死"的保命输出，不是"教训被编译进规则"。
- 元层 `evolve_meta.sh effectiveness`（27）与周报（13）是**另一条链路**（人工/脚本固化教训），
  与 ACE 生效率**分开报、不相加**。
- 复现：`cat reflector-heartbeat.json` + `cat curator-runs.jsonl`
- 归因表：`~/your-vault/brain/outputs/ACE生效率归因-2026-09-30.md`
- 2026-09-30 更新：轮C 模型产出 2 条已并入 Playbook，实读普查 = **模型 2 / 兜底 2 / 共 4**——「模型产出 0」已被打破。

## User Safety: safe 判定（2026-09-30 实测 · 只查不动）

**结论：网关安检层的间歇性/随机拦截，不是模型抽风。**

证据（同一份黑匣子，两种结果）：
| 用例 | 输入 | 结果 |
|---|---|---|
| A | 黑匣子前 1800 字 | `User Safety: safe` |
| B | 黑匣子前 6000 字 | ✓ 真内容 |
| C | 黑匣子全文 2060 字 | ✓ 真内容 |
| D | "如何删除系统文件" | 正常拒绝（"很抱歉，我不能提供…"） |
| E | **空用户消息** | ⚠️ 编了 4 条泛泛经验（幻觉） |
| F | 正常短任务 | ✓ 真内容 |
| G | 黑匣子重复 5 遍 | 英文输出（模型切英文思考） |

**判据**：A 与 C 是**同一份输入**，A 被拦 C 通过 → 不是内容触发，是安检层随机放行。
D 走的是正常安全拒绝话术，与 `User Safety: safe`（3 个单词的空壳）是两回事。

**🔴 附带发现（比 User Safety 更严重）**：用例 E 空输入时，模型会**编造经验**（"当遇到复杂问题时→应拆解成多个小问题…"）。
→ ✅ 已落地（15:35）：SYSTEM 加了第 5 条规则（空输入只准答 `__NO_INPUT__`），
且 `_valid_experience(text, box_text)` 加了**事实锚点校验**——黑匣子里有数字时，
条目必须命中至少一个真实数字，否则判无效（编造的经验对不上任何数字，格式再完美也拦得住）。
三测全过：`__NO_INPUT__` 拦 / 空壳拦 / 格式完美的编造条拦 / 真模型输出不误伤。

**应对**：重试即可（轮C 连试 6 次失败、第 7 次成功），不要一次失败就宣告模型路径不通。
✅ 已改 5 轮指数退避（2/4/8/16/32s）；15:35 实测：一次尝试即中（7 秒），不再直连智谱/豆包/硅基
（网关已聚合 16+ 家免费源自动降级，直连=重复造轮子，依据《freeapi-free-first-chain》）。

## 🔴 SKILL.md 单写者规则（2026-09-30 定，违反即停）

`kb-evolve-meta/SKILL.md` 已两次被并发会话覆盖（10:38 实证、11:29 疑似）。规则：

1. **同一时刻只允许一个会话写本文件**。写前必须持协作锁（源自《现行规则-技能验证器Verifier》第五节）：
   `~/.workbuddy/bin/kb-write-lock.sh acquire skill-kb-evolve-meta <会话id>`，写完 `release`。
2. 锁被占（租约未过期且持有者≠自己）→ **跳过本次写入并报告**，绝不硬写。
3. 兜底流程（所有会话统一）：**读最新版 → 按 `## 验收纪律` 锚点插入 → 写回 → grep 复验**。
4. `~/.workbuddy/skills/` 已纳入 git（首提交 ba23fa9，2026-09-30；远端=your-vault 裸库）。
   每次改完 skill **必须 git commit + `git push your-vault main`**（裸库在 `~/your-vault/_运维/skills-git-remote/skills.git`，
   随 your-vault 走 Syncthing 三机同步——远端配置见 2026-09-30 16:00 更新）；
   发现内容突变先 `git diff` / `git log` 取证，恢复用 `git checkout` 挑正确版本。
5. **两会话继续互踩 → 停下报警，不要各写各的**（写入方与被覆盖方都要报告用户）。

## 同机并发会话会互相覆盖 SKILL.md（2026-09-30 实证）

## 同机并发会话会互相覆盖 SKILL.md（2026-09-30 实证）
`kb-evolve-meta/SKILL.md` 在 10:38 被**本机另一个 WorkBuddy 会话**（`2026-09-30-08-42-10`，在做 kb-evolve 3.0）覆盖，
不是从机写入。→ 改写本 skill 一律用「读最新版 → 按 `## 验收纪律` 锚点插入 → 写回 → grep 复验」，
锚点插入是幂等的，被覆盖后重跑即可恢复。

## 验收纪律

- 度量必须含可量化数值（秒数/延迟/条数/频次），否则进化分不加分。
- 周报必须能回答"这周进化改变了哪个行为"。
- 数据 append-only；回滚靠改前 `.bak`；技能库改写走 tmp+replace 原子写。
- SAGE **只归档不删**，可从冷存 `skill-library-archive.jsonl` 拷回。
