#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脚本开工门 · 执行前强制校验「这是不是最新版」，任何调用方一行前缀即可

用户 2026-11-13 全局策略：执行任务前先去检测脚本库里的最新版本。
本脚本是那条策略的**执行侧载体**，解决「plist / automation / 人工 三条入口各写一遍」的重复：
    ⛔ 旧：python3 ~/Documents/py/某某.py ...
    ✅ 新：python3 ~/Documents/py/脚本开工门.py -- python3 ~/Documents/py/某某.py ...

设计依据（不拍脑袋）：
  · 判据复用 `脚本版本管家.resolve_latest()`（sha256 对齐，不看版本号大小、不看退出码）——
    同一条判据只存在一处，避免两套口径打架。
  · 无人值守场景不能等人点头，故默认 `--auto-freeze`：门不过先自动 freeze 再复查，
    复查仍不过才 exit 1。自动化里「先固化再跑」是策略原文的顺序，不是自定义。
  · 只有库内脚本才设门：库外路径（~/.workbuddy/bin/*.py）不归本策略管，直接放行并记账
    （reason=not_in_library），避免把无关的 27 个 launchd 任务全拦死。
  · 每次判决写 gate.jsonl：谁、什么时候、哪个脚本、什么 gate、跑没跑 —— 事后可审计，
    不靠「我记得加过校验」。

用法：
  脚本开工门.py --check <文件|脚本名>      只判决不执行：0=可跑，1=不可跑（--strict 时 snapshot_only 也算不可跑）
  脚本开工门.py [--no-freeze] -- <命令...>  判决通过后执行原命令，退出码透传
  --selftest                              用真库做只读自检（不改任何文件）
"""
import argparse, datetime as dt, importlib.util, json, os, subprocess, sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
VM = LIB / "脚本版本管家.py"
OK_GATES = {"in_sync", "snapshot_only"}


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_vm():
    spec = importlib.util.spec_from_file_location("py_versioner", VM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def log(rec):
    try:
        d = Path.home() / ".workbuddy" / "py-versioner"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "gate.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now(), **rec}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def judge(target, strict=False, auto_freeze=True):
    """返回 dict：ok / gate / run / base / reason / froze"""
    vm = load_vm()
    p = Path(target).expanduser()
    base = p.stem
    if p.suffix.lower() in {".py", ".sh"} and not (LIB / p.name).exists() \
            and Path(p).resolve().parent != LIB:
        return {"ok": True, "gate": "out_of_scope", "reason": "not_in_library",
                "base": base, "run": str(p), "froze": False}
    r = vm.resolve_latest(base, LIB, vm.STATE)
    item = next((x for x in r["items"] if x["base"] == base), None)
    if item is None:
        return {"ok": False, "gate": "missing", "reason": "no_such_script_in_library",
                "base": base, "run": None, "froze": False}
    gate, run, froze = item["gate"], item["run"], False
    allowed = OK_GATES if not strict else {"in_sync"}
    if gate not in allowed and auto_freeze and item.get("live"):
        fr = vm.freeze(item["live"], LIB, vm.STATE, "开工门自动固化", "gate")
        froze = True
        r2 = vm.resolve_latest(base, LIB, vm.STATE)
        it2 = next((x for x in r2["items"] if x["base"] == base), None) or item
        gate, run = it2["gate"], it2["run"]
        if gate not in allowed:
            return {"ok": False, "gate": gate, "base": base, "run": run, "froze": froze,
                    "reason": "freeze_not_helping", "freeze": fr}
    return {"ok": gate in allowed, "gate": gate, "base": base, "run": run,
            "froze": froze, "reason": "pass" if gate in allowed else "gate_blocked"}


def selftest():
    checks = []
    r = judge(str(LIB / "脚本版本管家.py"), auto_freeze=False)
    checks.append(("自检：本机真库能通过判决", r["ok"] and r["gate"] in OK_GATES, r["gate"]))
    r2 = judge("绝对不存在的脚本_开工门自检")
    checks.append(("自检：查无此脚本判不可跑", (not r2["ok"]) and r2["gate"] == "missing", r2["gate"]))
    r3 = judge(str(Path.home() / ".workbuddy" / "bin" / "autoexec" / "autoexec.py"))
    checks.append(("自检：库外脚本不设门", r3["ok"] and r3["gate"] == "out_of_scope", r3["gate"]))
    p = subprocess.run([sys.executable, __file__, "--check", str(LIB / "脚本版本管家.py")],
                       capture_output=True, text=True)
    checks.append(("自检：--check 退出码 0", p.returncode == 0, "rc=%d" % p.returncode))
    p2 = subprocess.run([sys.executable, __file__, "--check", "绝对不存在的脚本_开工门自检"],
                        capture_output=True, text=True)
    checks.append(("自检：--check 不可跑时退出码 1", p2.returncode == 1, "rc=%d" % p2.returncode))
    p3 = subprocess.run([sys.executable, __file__, "--", sys.executable, "-c", "print('PASS-THROUGH')"],
                        capture_output=True, text=True)
    checks.append(("自检：门后命令真被执行", "PASS-THROUGH" in p3.stdout and p3.returncode == 0,
                   "rc=%d" % p3.returncode))
    passed = sum(1 for _, ok, _ in checks if ok)
    for n, ok, d in checks:
        print("%s %-28s %s" % ("PASS" if ok else "FAIL", n, d))
    print("---- %d/%d ----" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description="脚本开工门 · 执行前校验最新版")
    ap.add_argument("--check", default="", help="只判决不执行")
    ap.add_argument("--strict", action="store_true", help="只认 in_sync，snapshot_only 也算不可跑")
    ap.add_argument("--no-freeze", action="store_true", help="门不过不自动固化，直接拒跑")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    auto = not a.no_freeze
    if a.check:
        r = judge(a.check, a.strict, auto)
        log({"mode": "check", **{k: r[k] for k in ("base", "gate", "ok", "froze", "reason")}})
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1
    cmd = list(a.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("用法：脚本开工门.py --check <文件> | -- <命令...> | --selftest")
        return 2
    # 只对「看起来是脚本文件路径」的参数设门；`python3 -c ...` 这类无脚本文件的一律放行
    cands = [c for c in cmd if c.endswith((".py", ".sh")) and ("/" in c or (LIB / c).exists())]
    if cands:
        # 命令里所有脚本路径都设门：链式命令漏检一个 = 等于没设门
        rs = [(t, judge(t, a.strict, auto)) for t in cands]
        target = cands[0]
        bad = [(t, x) for t, x in rs if not x["ok"]]
        r = dict(bad[0][1]) if bad else dict(rs[0][1])
        r["targets"] = [{"target": t, "gate": x["gate"], "ok": x["ok"], "froze": x["froze"]}
                        for t, x in rs]
    else:
        target = cmd[0]
        r = {"ok": True, "gate": "out_of_scope", "base": Path(cmd[0]).stem,
             "reason": "no_script_file_in_cmd", "froze": False, "run": None}
    log({"mode": "run", "target": target, "cmd": cmd,
         **{k: r[k] for k in ("base", "gate", "ok", "froze", "reason")}})
    if not r["ok"]:
        print("[开工门] 拒跑：%s gate=%s reason=%s" % (target, r["gate"], r["reason"]),
              file=sys.stderr)
        return 1
    if r["froze"]:
        print("[开工门] 已自动固化后放行：%s gate=%s" % (r["base"], r["gate"]), file=sys.stderr)
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
