---
name: skill-curator
description: 技能策展器——让技能库「越用越聪明」而非「越用越臃肿」。用真实调用遥测判断哪个技能在用、哪个在死，自动分级（active/stale/待归档），只归档不删除、可一键恢复，出审计账本和技能学习旅程时间线。当用户说「技能太多」「哪些技能没人用」「技能库该清理了」「技能策展」「技能复盘」「技能用了多少」「学习旅程」「让技能库自动进化」「Hermes 那个越用越聪明」时使用。
agent_created: true
---

# 技能策展器（Hermes curator 对标）

> 灵感来源：Hermes 的 `hermes curator` + `hermes journey`。
> 移植时间：2026-11-04。核心是把 Hermes 的「技能自维护闭环」搬进 WorkBuddy。

## 一、为什么需要它

WorkBuddy 的技能库只增不减：现在 59 个技能，其中 44 个是 `agent_created`（Agent 自己写的）。
《kb-evolve-meta》早就诚实标注过一个局限：

> `frequency` 是**词法代理指标**（被多少篇笔记共同提到），**不是真实触发计数**。
> 真实计数需 Reflector/Curator 在使用时回填 `last_used`（**尚未接通**）。

**「尚未接通」的那一环，现在接上了。** 本技能用 PostToolUse hook 捕获真实的技能调用，
让「闲置天数」有真实数据支撑，而不是拿文件 mtime 猜。

Hermes 原版靠 `~/.hermes/skills/.usage.json` 喂数据给 curator，
WorkBuddy 这边此前**完全没有**技能调用遥测 —— 这是本技能补的核心缺口。

## 二、三件套

```
真实调用遥测 (skill_usage.py)  →  策展判定 (skill_curator.py)  →  成长回顾 (skill_review.py)
     ↑ PostToolUse hook                 ↑ 分级/归档/账本/回滚          ↑ 时间线/触发率/报告
```

| 文件 | 位置 | 职责 |
|---|---|---|
| 遥测采集 | `~/.workbuddy/bin/skill-curator/skill_usage.py` | PostToolUse hook 捕获技能调用，写 `usage.jsonl` |
| 策展器 | `~/.workbuddy/bin/skill-curator/skill_curator.py` | 分级、归档、账本、备份、回滚 |
| 回顾 | `~/.workbuddy/bin/skill-curator/skill_review.py` | 学习旅程时间线 + 触发率报告 |
| 调度入口 | `~/.workbuddy/bin/skill-curator/skill-curator-run.sh` | 周一 04:10 跑 dry-run 报告 |
| 定时器 | `com.scg.skill-curator` | launchd，已登记进 idle-scheduler B 类名单 |
| 状态目录 | `~/.workbuddy/state/skill-curator/` | usage.jsonl / ledger.jsonl / archive/ / backups/ |

## 三、常用命令

```bash
PY=${PYTHON}
C=~/.workbuddy/bin/skill-curator/skill_curator.py
R=~/.workbuddy/bin/skill-curator/skill_review.py
U=~/.workbuddy/bin/skill-curator/skill_usage.py

$PY $U --stats          # 技能调用遥测统计
$PY ~/.workbuddy/bin/skill-curator/skill_usage_backfill.py --dry-run   # 遥测历史回填预览
$PY ~/.workbuddy/bin/skill-curator/skill_usage_backfill.py --apply     # 真回填（先自动备份）
$PY $C status           # 策展状态（各状态数量 + 最久未用榜）
$PY $C report           # 生成策展报告（dry-run，不动文件）
$PY $C apply            # dry-run 归档预览
$PY $C apply --apply    # 真归档（需人工拍板）
$PY $C pin <技能名>      # 钉住，策展永不触碰
$PY $C unpin <技能名>
$PY $C ledger           # 审计账本
$PY $C rollback <id>    # 回滚某次归档
$PY $R                  # 技能学习旅程（终端速览 + 写报告）
$PY $R --json           # JSON 输出，供上层可视化
```

## 四、策展规则（对齐 Hermes）

| 状态 | 判据 | 处置 |
|---|---|---|
| `active` | 闲置 < 14 天 | 保留 |
| `stale` | 闲置 ≥ 14 天 | 标记观察，不动 |
| `archive_candidate` | 闲置 ≥ 30 天 **且无活依赖** | **候选**（归档需人工拍板） |
| `dependent` | 🔒 有活任务/脚本/夜间 prompt 依赖 | **永不自动归档** |
| `pinned` | 在 `pinned.txt` 里 | 永不触碰 |
| `protected` | 非 `agent_created`（bundled/hub 装的） | 永不触碰 |

### 与 Hermes 的三处刻意差异

1. **默认不自动归档**（Hermes 是自动的）
   理由：Hermes 侧有 7 天周期的真实遥测做底，且归档的是它自己生成的技能；
   本机遥测刚起步，归档的是用户亲手写的技能 —— 不可逆风险不对称。
   归档动作留给人拍板，判定逻辑全自动。

2. **砍掉 LLM 合并重叠技能**（Hermes 侧 `consolidate` 本身也是 off）
   本地自动改写别人写的技能风险太高。重叠检测列为待办，**宁可列候选也不自动合并**。

3. **归档区放在 `state/` 而非 `skills/.archive/`**
   WorkBuddy 扫描 `~/.workbuddy/skills/*/SKILL.md`。归档区放到
   `~/.workbuddy/state/skill-curator/archive/` —— **完全不在 skills/ 下**，
   零风险不会被加载。恢复 = 拷回 skills/。

## 五、五道安全闸

1. **只归档不删除** —— 归档 = 移动到 `state/skill-curator/archive/`，原件完整保留，可 `rollback` 恢复。
2. **只处理 `agent_created: true`** —— bundled / hub 安装的技能永不触碰（与 Hermes 同规则）。
3. **🔴 活依赖检查（坑 9 · 2026-11-04 加）** —— 「闲置」≠「没用」。
   技能可能靠**别的技能 / 脚本 / 夜间编排 prompt** 间接被用。
   自动检查：活的 launchd plist + `~/.workbuddy/bin/` 下活脚本（排除 `.bak`/`__pycache__`）
   + `night-orchestrator/prompts/*.md` + `workbuddy.db` 的活自动化任务表。
   **有活依赖 → 标 `dependent`，永不自动归档。**
4. **动手前必备份** —— 每次 apply 前 tar 快照 `skills-*.tar.gz`，保留最近 5 份。
5. **审计账本 append-only** —— 每次变更写 `ledger.jsonl`，记录 before/after 路径、证据、备份路径。

## 六、遥测是怎么采的

挂 WorkBuddy 的 **PostToolUse** hook（`~/.workbuddy/settings.json`）：

```json
{ "hooks": { "PostToolUse": [ {
  "matcher": "Skill|search_and_install_skills|skill_management|DeferExecuteTool",
  "hooks": [ { "type": "command",
    "command": "<python> .../skill_usage.py --hook", "timeout": 5 } ] } ] } }
```

**只记结构字段，不记正文** —— `prompt` / `tool_response` 全文 / `args` 自由文本一律不落盘，
只记「哪个技能、什么时候、哪个会话、什么动作」。遥测要的是"用没用过"，不是"用了什么"。

**fail-open 铁律**：hook 任何异常都 exit 0。写坏的 hook 会卡死整条工具链
（实测：别的 hook 超时 8 秒 → 数分钟所有工具调用被拦）。

⚠️ **hook 是启动时快照的** —— 新挂的 hook 需要**重启 WorkBuddy** 才生效。
重启前遥测是空的；这也是为什么 `apply` 初期判据偏保守。

## 七、踩过的坑（已固化进代码）

1. **deferred 工具的形态不同** —— `search_and_install_skills` / `skill_management` 经
   `DeferExecuteTool` 调用时，外层 `tool_name` 是 `DeferExecuteTool`，真名藏在
   `tool_input.toolName`，真参数在 `tool_input.params`。只按 `tool_name` 匹配会漏掉一半事件。
2. **`skill_management` 的技能名是数组** —— `skill_names: [...]`，不是单个 `name`。
   `action=list` 不带技能名 —— 那是管理动作，不是"用了某技能"，正确地不记。
3. **`restore_skill` 硬编码路径会污染真实技能目录** —— 自测暴露：临时归档被恢复到
   真实 `~/.workbuddy/skills/` 下。现已改为 `skills_dir` 参数可注入，自测加了回归断言。
4. **自测计数写死会掩盖新用例** —— 写死 "22/22" 后新增 3 个用例，脚本仍报 22。
   现改为**动态计数**（`total` 变量）。这是通用教训：
   **改了不复测 = 把没验证包装成验证过**。
5. **`launchctl bootstrap` 返回 rc=5 不一定是失败** —— 可能是已加载。
   真相看 `launchctl print gui/$(id -u)/<label>`，不看 rc。
6. **launchd 日志路径不能指向脚本文件本身** —— 必须是目录，否则 exit=78 静默死亡。
   本机日志统一写 `~/.workbuddy/logs/com.scg.skill-curator.log`。
7. **🔴 遥测空白期会把 mtime 当最后使用**（坑 10，见第八节）——
   遥测覆盖率低时闲置判定全是猜测，会静默变成归档决策。
   真数据在 `~/.workbuddy/traces/` 里，回填即可；`status` 里先看覆盖率。
8. **🔴 验证「定时器有没有真跑」不能查 launchd domain** ——
   B 类任务被 idle-scheduler 挂起时**本来就不在 domain 里**，
   `launchctl print` 报 "Could not find service" 是正常态，
   拿它当判据 = 判据恒假 = 永久失败（实测 4 条 autoexec 项全栽在这）。
   正确判据：**任务自己的日志痕迹 + idle-scheduler.log 的「放行(load)」记录**。
   （selfcheck 的 D5 检查项会自动拦截这类恒假判据）

## 八、当前实况（2026-11-04 体检后实测）

- 技能总数 60 → **45**（已归档 15 个）
- 遥测已生效（19:42:44 实测捕获本会话真实调用）
- **遥测覆盖 11 / 45 个技能**（体检后回填了 48 条历史，1 → 11）
- 首轮策展：36 个候选 → **pin 21 个 + 归档 15 个**
  - pin 21 = 活依赖 16 + 核心机制入口 5
  - 归档 15 = 零活依赖 + 非核心入口
  - **零误伤验证**：15 个归档技能与 trace 历史里的真实调用记录**交集为空**
    （即它们确实一次都没被用过，不是被误判）
- **坑 9 的实测数据**：36 个「从没被用过」的候选里，**16 个被活任务依赖**
  （`night-orchestrator/prompts/skill_retro_daily.md` 每晚引用 8 个），
  另有 5 个是核心机制入口（`skill-verifier` / `self-evolution-loop` /
  `your-vault-local-kb` / `silent-failure-rootcause` / `token-saver`）。
  **单看「闲置天数」会误伤 21 个。**
- 归档全部可 rollback（实测：回滚 1 条 → 回到 skills/ → 再归档回去，全通）
- 归档动作一律等人工拍板，策展周检只出报告

### 🔴 坑 10（2026-11-04 体检发现）：mtime 不是使用信号

遥测靠 hook 采集，**hook 是启动时快照** —— 新装的遥测只有个位数记录，
其余技能的「最后活跃」退化成 `max(SKILL.md 文件 mtime, 真实调用)`，
而 mtime 对没被 hook 记过的技能 = **文件最后一次被编辑距今**，
跟「这个技能有没有被用过」完全是两回事。

实测：**11 个有真实调用记录的技能里 5 个被误判成 ≥30 天闲置**
（`your-vault-local-kb` 昨天还在用，mtime 判它 32 天没用过）。
它们没被误归档，**不是因为判据对，是因为都在 pinned.txt 里** —— pin 是人，不是机制。

**解法**：`skill_usage_backfill.py --apply` 从 `~/.workbuddy/traces/*/*.jsonl`
（2706 个文件、3 周历史）回填真实调用。已挂进周检第 1 步。
回填后 5 个误判全部纠正（32→1.4 / 42→16.5 / 36→19.6 / 33→16.3 / 31→16.9 天）。

⚠️ 幂等键坑：hook 记 WorkBuddy `session_id`，trace 记 `traceId`，
用 `(skill, ts, session)` 做键 → 同一次调用被算两次。
正确做法：**同技能 + |Δts| < 300 秒**判同一次事件。

⚠️ **看遥测覆盖率再信判据**：`status` 里「有遥测记录」是 N/总数，
N 太小时所有闲置判定都是猜测。

## 九、诚实声明

- 遥测只覆盖 **PostToolUse 捕获到的技能工具调用**；插件内置技能不在此列。
- 「从未触发」≠「没用」—— 也可能是触发词写太软，会话里压根没想起它。
  策展按闲置天数分级，但**归档需人工判断**。
- 闲置天数在遥测积累初期以文件 mtime 估算，**偏保守**（更容易判闲置），
  但仍可能误伤冷门但有用的技能 —— 这也是默认不自动归档的原因之一。
