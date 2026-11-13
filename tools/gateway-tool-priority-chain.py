#!/usr/bin/env python3
"""为免费网关建一条「工具能力优先」的 profile 链（auto:tools）。

为什么要有（2026-11-11 用户指令：优化 Hermes 监工模式的模型路由，
「有的模型没有调动工具的能力，怎么去优化这种大模型的路由？或主动地帮它切换」）：

  实测根因（本机 freellmapi DB 事实）：
  · Default 链 709 个模型，其中 **189 个 supports_tools=0**；
    从 priority 63 起就开始穿插不支持工具的模型。
  · Hermes 主模型走 `auto` → 一旦降级到那些模型，**不会发起 tool_calls**，
    于是活干不了（黑匣子原话：「AI无法执行本地命令」）。
  · FreeFirst 链 37 个全支持工具；codex 链 13 个里 12 个支持。
  → 缺一条「保证全程都会调工具」的专用链。

做法（依据《2026-09-25-免费网关路由优化：静态评分不可信，改用实测数据排序》）：
  **排序键 = 本地实测成功率（尝试≥15 次的）**，不是目录里的智能度分；
  同成功率再按尝试次数多的优先（样本大更可信）；无实测的排最后。
  只收 supports_tools=1 的模型。

安全：
  · 只在新建/更新 profile_models 时动；不碰 models 表、不碰凭据。
  · 改前自动备份 DB（cp freeapi.db → .bak-toolvc-<ts>.db）。
  · 幂等：重复跑结果一致；提供 --dry 预览。
  · 回滚：--rollback 恢复备份里的链。

用法：
    python3 网关工具优先链.py --dry        # 只看会建成什么样
    python3 网关工具优先链.py             # 建链
    python3 网关工具优先链.py --rollback  # 回滚
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path.home() / "freellmapi/server/data/freeapi.db"
PROFILE_NAME = "Tools"
MIN_ATTEMPTS = 15        # 库里的既有口径：尝试 ≥15 次才算有效样本
MAX_CHAIN = 60            # 链长上限：太长会让降级慢，太短会断供
BACKUP_SUFFIX = ".bak-toolvc"


def conn() -> sqlite3.Connection:
    return sqlite3.connect(str(DB))


def build_chain(c: sqlite3.Connection, limit: int = MAX_CHAIN) -> list[tuple[str, str]]:
    """按实测成功率挑出「支持工具」且「实测可靠」的模型，返回 [(platform, model_id)]。"""
    q = """
    WITH att AS (
      SELECT platform, model_id,
             SUM(CASE WHEN outcome='ok' THEN 1 ELSE 0 END) ok_n,
             COUNT(*) total_n,
             AVG(duration_ms) avg_ms
      FROM request_attempts
      WHERE outcome IS NOT NULL
      GROUP BY platform, model_id
      HAVING total_n >= ?
    )
    SELECT m.platform, m.model_id, m.id AS model_id_pk,
           COALESCE(att.ok_n,0) ok_n,
           COALESCE(att.total_n,0) total_n,
           COALESCE(1.0*att.ok_n/att.total_n, -1) pct,
           COALESCE(att.avg_ms, 999999) avg_ms
    FROM models m
    LEFT JOIN att ON att.platform=m.platform AND att.model_id=m.model_id
    WHERE m.enabled=1 AND m.supports_tools=1
    ORDER BY pct DESC, total_n DESC, avg_ms ASC
    LIMIT ?
    """
    rows = c.execute(q, (MIN_ATTEMPTS, limit)).fetchall()
    return [(r[0], r[1]) for r in rows], rows


def ensure_profile(c: sqlite3.Connection) -> int:
    row = c.execute("SELECT id FROM profiles WHERE name=?", (PROFILE_NAME,)).fetchone()
    if row:
        return row[0]
    mx = c.execute("SELECT COALESCE(MAX(sort_order),0) FROM profiles").fetchone()[0]
    c.execute(
        "INSERT INTO profiles (name, emoji, color, type, is_favorite, sort_order, auto_include_new_models)"
        " VALUES (?,?,?,?,?,?,0)",
        (PROFILE_NAME, "🛠️", "#10b981", "custom", 1, mx + 1))
    c.commit()
    return c.execute("SELECT id FROM profiles WHERE name=?", (PROFILE_NAME,)).fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--limit", type=int, default=MAX_CHAIN)
    a = ap.parse_args()

    if not DB.exists():
        print(f"❌ 网关 DB 不存在：{DB}")
        return 2
    c = conn()

    if a.rollback:
        baks = sorted(DB.parent.glob(DB.name + BACKUP_SUFFIX + "*"))
        if not baks:
            print("❌ 没有可回滚的备份")
            return 2
        src = baks[-1]
        shutil.copy2(src, DB)
        print(f"✅ 已从 {src.name} 回滚")
        return 0

    chain, rows = build_chain(c, a.limit)
    if not chain:
        print("❌ 没选出任何模型")
        return 2

    print(f"拟建链「{PROFILE_NAME}」→ auto:{PROFILE_NAME.lower()}  共 {len(chain)} 个（全是 supports_tools=1）")
    for plat, mid in chain[:20]:
        r = next((x for x in rows if x[0] == plat and x[1] == mid), None)
        pct = f"{r[5]*100:.1f}%" if r and r[5] >= 0 else "无实测"
        n = f"{r[4]}" if r else "0"
        print(f"   {plat:<14} {mid:<52} 成功率={pct:<8} 样本={n}")
    if len(chain) > 20:
        print(f"   … 共 {len(chain)} 个")

    if a.dry:
        print("\n（DRY：未写入）")
        return 0

    # 备份 DB
    bak = DB.parent / f"{DB.name}{BACKUP_SUFFIX}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(DB, bak)
    print(f"\n已备份 DB → {bak.name}")

    pid = ensure_profile(c)
    c.execute("DELETE FROM profile_models WHERE profile_id=?", (pid,))
    ids = {}
    for plat, mid in chain:
        r = c.execute("SELECT id FROM models WHERE platform=? AND model_id=? LIMIT 1",
                      (plat, mid)).fetchone()
        if not r:
            continue
        if r[0] in ids:
            continue
        ids[r[0]] = True
        c.execute("INSERT INTO profile_models (profile_id, model_db_id, priority, enabled)"
                  " VALUES (?,?,?,1)", (pid, r[0], len(ids)))
    c.commit()

    n = c.execute("SELECT COUNT(*) FROM profile_models WHERE profile_id=?", (pid,)).fetchone()[0]
    # 校验：链里必须全是支持工具的
    bad = c.execute(
        "SELECT COUNT(*) FROM profile_models pm JOIN models m ON m.id=pm.model_db_id"
        " WHERE pm.profile_id=? AND m.supports_tools=0", (pid,)).fetchone()[0]
    print(f"✅ 已写入：链长 {n}，其中不支持工具的 {bad} 个（必须为 0）")
    print(f"   用法：model=\"auto:{PROFILE_NAME.lower()}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
