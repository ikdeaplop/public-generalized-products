---
name: skill-verifier
description: 给技能库当"考官"——新技能/改动必须过考试才准进库，CoEvoSkills 式双角色（Generator 提炼改写 / Verifier 出卷阅卷）。当用户说"验证技能/给技能加考官/skill 质量把关/证明 skill 有用/生效率太低"，或准备把新提炼的经验、踩坑、技能写进 kb-evolve 技能库时使用。L1 结构验证零成本必做，L2 隔离环境复现能做才做，做不了诚实标 L1_ONLY。也用于排查「jsonl 共享状态被并发写者改坏/条目莫名减少」。
agent_created: true
---

# 技能验证器 Verifier（过验证才准进库）

## 何时触发
- 准备把新提炼的技能/经验/踩坑**写进技能库**（`~/.workbuddy/state/kb-evolve/skill-library.jsonl`）。
- 有人质疑"这技能有用吗 / 生效率多少"（实测：140 条 `success_rate` 全为 null）。
- CoEvoSkills 式"共进化"：技能与其考试卡一起迭代。
- **jsonl 行莫名变少、怀疑被并发写坏**——第五节有当日实测的根因与排查顺序。

**为什么必须有这道关**：SkillsBench 指出人写的 skill 有时反而有害。实测本库 140 条里
139 条 trigger 只是标题复述、仅 40 条含可执行代码块、31 条 approach 退化成 `（见原避坑记录）`。

## 两级验证（按成本，先 L1）

**L1 结构验证**（自动、0 成本、必做）

| 编号 | 判什么 |
|---|---|
| L1-COMPLETE | 字段齐（trigger/approach/frequency/success_rate/source） |
| L1-TRIGGER-SPEC | trigger 能回答"什么时候触发"：非标题复述、≥8 字、含症状信号词 |
| L1-ACTION-EXEC | approach 可执行：≥12 字、有命令块或编号步骤、非空话、**非截断残句** |
| L1-DEDUP | 与已入库重复，比 trigger **还要比标题**（id 由 trigger 派生，改 trigger 会换 id） |
| L1-PORTABILITY | 本机专属/密钥：轻则 warn，**含密钥原文直接打回** |
| L1-DOCTYPE | **这是不是一条技能**：规范/模板/索引/速查表类文档一律剔除 |
| L1-NOTSTALE | source 文件存在（按 `~/your-vault/资料库/` 等多基准解析） |

**L2 复现验证**（能复现才做）
临时目录 + 20s 超时 + 危险命令黑名单。**自动抽取的考试卡默认不许跑**——
实测自动抽出来的全是散文碎片，不是命令。合格考试卡要同时验证：
1. 坑真实存在（错误写法能复现故障）
2. 技能给的解法管用（推荐写法能跑通）

## 执行流程
```bash
PY=${PYTHON}
V=${WB_BIN}/kb-evolve/verifier.py
G=${WB_BIN}/kb-evolve/generator.py

$PY $V --submit <候选.jsonl>   # 候选进待验证区，不直接入库
$PY $V --exam --id <id>        # 生成/挂载考试卡
$PY $V --run                   # 考试（不合格输出 reason + fix）
$PY $G --revise                # Generator 按反馈重写
$PY $V --run                   # 复考
$PY $V --promote               # 通过者进主库（同 id 就地升级）
$PY $V --audit --apply         # 历史无凭据条目落 legacy-unverified
$PY $V --report                # 驳回率与原因分布
```
Voyager 的产出已默认改道到待验证区（`VOYAGER_GATE=0` 可临时关闭闸门排障）。

## 诚实标注（写进 `verified` 字段）
`L2` = 复现通过 ｜ `L1_ONLY` = 仅结构验证、**未复现** ｜ `legacy-unverified` = 无验证凭据。
**不许把没验证写成验证通过**；L2 跑不了就写 L1_ONLY，`success_rate` 保持 null。

## 写库保护（必走）
主库写入只有 `guarded_write_lib()` 一个入口：**坏行拒绝 → 持锁 → 快照 → 原子写 → 记指纹**。
- 锁：`~/.workbuddy/bin/kb-write-lock.sh acquire skill-library.jsonl <sid>`（协作式，防不住第三方）
- 快照：`state/kb-evolve/snapshots/` 滚动保留 20 份
- 漂移检测：写前比对上次指纹，被别人动过会明确报出来

## 关键坑（血泪，别再踩）
1. **考官自身会有假阳性**：某项命中率异常整齐（100% 全中）时，先查检查器。
   实例：`source` 是库内相对路径，基准是 `~/your-vault/资料库/`，漏拼前缀导致 12/12 误判文件不存在。
2. **失败原因要指准**，否则重写被带偏。approach 是残句时报"缺步骤"，Generator 会去补步骤而残句照旧
   → 单独做截断检测，指明"回源文件取完整段落"。
3. **🔴 并发读改写会静默蒸发数据**（2026-09-30 实测丢 52 条）：
   `load_lib()` 用 `try/except: pass` **静默跳过坏行**，`save_lib()` 又把"解析出来的那些行"整份写回
   → 半行损坏即永久删除且零报错。真凶是**另外两个并行 WorkBuddy 会话**在同跑同一条流水线。
   **JSONL 的 load 捕获坏行必须显式上报，绝不能 pass。**
   排查顺序：先看 launchd（排除定时任务）→ 再看 watcher 日志（确认 Listen 目录不含 state）
   → 最后看 `~/.workbuddy/projects/` 下有没有别的并行会话。别一上来就猜"未知后台进程"。
4. **恢复用联合（union），不要覆盖**：当时 current 的 100 条里有 12 条是 backup 里没有的新条目。
5. **同 id 入库要就地升级**，追加会产生重复行。

## 关联
规则全文：`~/your-vault/资料库/07-协作与规范/现行规则-技能验证器Verifier-L1L2两级-20260930.md`
踩坑原始记录：`~/your-vault/资料库/08-踩坑记录/2026-09-30-kb-evolve主库被并发写者重写-140条被挤到100.md`
考试流水（append-only）：`~/.workbuddy/state/kb-evolve/verifier-ledger.jsonl`
