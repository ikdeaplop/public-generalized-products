# 上下文经济学 · 详版

**何时读取本文件**：走 B 路径（上下文治理）；或需要审计 / 改造某个 skill 的体积与加载结构时。

---

## 铁律 1 · 读之前先问代价

```bash
python3 scripts/context_cost.py FILE-OR-DIR        # 估 token 成本
python3 scripts/context_cost.py --top 20 DIR       # 找出目录里最贵的 20 个文件
```

| 估算 token | 处置 |
|---|---|
| 低于 2k | 直接读 |
| 2k–8k | 先 Grep 定位行号，再 `Read(offset,limit)` 读局部 |
| 超过 8k | **禁止整读**。改用脚本 parse / 专门检索脚本 / Grep 抽取片段 |

- **大 JSON / CSV / 日志：一律脚本 parse，绝不 Read。** 需要反复查就写一个带 `--search` / `--detail` 的小脚本，单次查询几十 token。
- **同一个文件不读第二次。** 已读过的结论直接复用，不要为"再确认一下"重读。

## 铁律 2 · 输出不回显已知道的内容

- 内容超过约 200 行或 2000 字 → **落文件**，回复只给「结论 + 关键数字 + 文件路径」。
- **不复述工具输出原文**（不要把命令 stdout 整段抄进回复）。
- **最终回复控制在 50–70 行**，高信号优先。
- 中间过程的进度描述写短，结论写全。

## 铁律 3 · 能并行就并行

多个**互相独立**的工具调用放进同一个 block 一次发出（读文件、glob、grep、web 搜索）。串行等待不省 token，只浪费轮次。

## 铁律 4 · 大上下文用子代理隔离

需要翻遍代码库或大量网页才能回答的问题 → 派 `Explore` 子代理，并在 prompt 里明确 **"报告控制在 200 字以内，只给结论和文件路径，不要贴原文"**。子代理的中间消耗不进主上下文。

## 铁律 5 · 搜索一次问清

- WebSearch 的 query 写完整（含实体名、时间范围、限定条件），一次拿全，不要同一主题反复搜。
- 多个独立关键词并行搜，不要排成一串。

## 铁律 6 · 复用而非重算

- 已生成的中间产物（脚本、报告、索引）存到工作区，下次直接调用。
- 重复 3 次以上的操作固化为 `scripts/` 里的脚本 —— 执行只花输出 token，不花源码 token。

---

## 反模式清单（看到就改）

| 反模式 | 正解 |
|---|---|
| `Read` 整个 200KB JSON | 写检索脚本，按需查 |
| `cat` 整个文件看内容 | `scripts/context_cost.py` 估成本 → Grep → 局部 Read |
| 反复 Read 同一个文件 | 记结论，不重读 |
| 把命令输出整段抄进回复 | 摘要 + 关键行 |
| 把 10 个语种模板全读一遍 | 只读当前语种那一个 |
| 逐条串行调用独立工具 | 同一 block 并行发出 |
| 为"确认"重复跑同一命令 | 一次跑对，结果记牢 |
| 读输出模板 / 资产文件进上下文 | 复制使用，不读源 |

---

## 改造 skill 的检查清单

按**渐进式披露**三级加载：metadata（常驻约 100 词）→ SKILL.md body（触发时，目标 5k 词内）→ references（按需）。

1. **SKILL.md 只留决策逻辑**：判断条件、工作流、铁律。**可查的细节（示例库、模式库、字段表、规范、坑清单）一律下沉 `references/`。**
2. **每个 reference 必须在 SKILL.md 有索引**，写明"何时加载它"。没被索引的 reference = 幽灵文件（agent 要么不用，要么乱翻）。
3. **每个 reference 顶部写一行"何时读取本文件"**，让 agent 二次确认是否真需要。
4. **大 reference 配检索脚本**：`scripts/*_lookup.py --search/--detail`，禁止 Read 源文件。
5. **脚本放 `scripts/` 不放 `references/`** —— references 会被当文档读，脚本不该占上下文。
6. **资产文件（模板 / boilerplate）放 `assets/`**，复制使用不读源。
7. **标注"必读"文件**：少数 reference 是质量底线（如品牌事实库），明确写"不可省"，防止为省 token 跳过导致编造数据。

---

## 审计现有 skill

```bash
python3 scripts/context_cost.py --top 30 ~/.workbuddy/skills/SKILL-NAME
```

重点看三列：**SKILL.md 是否过大**（每次触发固定开销）、**有无超过 8k 的 reference**（误读炸弹）、**references 是否都在 SKILL.md 里有索引**（幽灵文件）。

---

## 诊断上下文开销（拿到真实数据后）

```bash
python3 scripts/prompt_profiler.py PROMPTS-DIR     # 量化「固定开销占比」
python3 scripts/prompt_profiler.py prompts/ --min-dup 3 --verbose
python3 scripts/prompt_profiler.py --log metrics.csv --days 14 --price-in 0.003 --price-out 0.015
python3 scripts/prompt_profiler.py --init-template metrics.csv    # 生成埋点模板
```

- **prompt 样本分析**：找出在不少于 2 个样本中重复出现的连续块 → 这就是固定开销，可走 Prompt Caching / 模板化 / 知识库外置。输出直接给出「可省 X token/轮，占 Y%」。
- **埋点日志分析**：按场景输出 P50/P95、月总 token 估算、成本估算，并按月总消耗排出优化优先级（帕累托：累计到 80% 的那几个场景就是 ROI 最高的目标）。

埋点只需 4 个字段：`scene, t_in, t_out, latency_ms`。没有基线就无法证明收益，所以这是任何省 token 项目的第一步。
