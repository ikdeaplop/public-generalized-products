#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脚本版本管家 · 脚本后面加版本号，每次更改自动存为新版本

规则（用户 2026-11-11 定，跨项目长期生效）：
  · 活文件：`名称.py` —— 始终是当前可编辑版本，保证既有引用（自动任务 / KB 文档）不失效。
  · 版本快照：`名称 V1.py`、`名称 V2.py` … —— 第一版 V1，依次加一，永不覆盖。
  · 每次更改活文件 → 自动生成下一个 V 快照；内容不变不产生新版本。

设计依据（2026-11-11 实调研，禁止拍脑袋）：
  · fswatch 1.22.0 + launchd KeepAlive：开源 liorroy/zsh-auto-backup 与
    szymonkrajewski.pl《Automatic file versioning after change using fswatch and git》
    的成熟组合；`fswatch -o -l 1.5` 批处理去抖，排除隐藏文件与版本快照自身。
  · 内容 sha256 判重（不用 mtime）：从《技能策展体检（2026-11-04）· 遥测空白期把
    mtime 当最后使用》拿到「mtime 根本不是内容信号」；touch 一下不许生成假版本。
  · 成功判据校验业务产物（不看退出码）：从《晨报「✅ 成功」但业务 0 篇》拿到
    「只按子进程退出码判成功」的坑；新版本必须真实存在 + hash 一致 + 号递增 + 账本有记录。
  · 调度源必须可核对：同上「注释写由 launchd 触发但实际无 plist」的谎报，
    status 里用 `launchctl list` 实证，不靠注释自称。
  · 改源头而非只改 plist：从《日志清了又长：改 plist 没用，要改生成它的脚本》拿到，
    所以核心 snapshot() 被 save / scan / watch 三入口复用，watch 只是触发源。

命令：
  init                       为脚本库所有活文件建 V1 基线（已存在则 unchanged）
  freeze <file> [--note TXT]  【固化】语法预检 → 存为新版本 → 回读校验，一条命令落地
                             用户 2026-11-13 定：每次脚本更新的第一动作就是固化入库，
                             且部署落地对象只能是活文件（不带 V 号）。
  save <file> [--note TXT]    显式存为新版本
  scan                       批量扫描活文件，变化者自动存新版本（watch 触发时调用）
  watch                      fswatch 常驻，目录变更即 scan（launchd 调用）
  list --name NAME           列出某脚本的所有版本
  latest [KEY|--name KEY]    【开工门】执行任何任务前先解析该跑哪个文件：返回 run 路径 +
                             活文件与最高快照是否 in_sync（不同 = 改了没固化，必须先 freeze）
                             不给 KEY = 全库体检，列出所有需要固化的脚本
  status                     验活：服务 / 进程 / 同步 / 账本 / 调度源真值
  install-service            生成并加载 launchd plist（可逆）
  uninstall-service          卸载并把 plist 移入 _disabled（不删除）
  --selftest                 隔离临时目录自检，不污染真库
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time
import datetime as dt
from pathlib import Path

HOME = Path.home()
LIB = HOME / "Documents" / "py"
STATE = HOME / ".workbuddy" / "py-versioner"
PLIST = HOME / "Library" / "LaunchAgents" / "com.scg.py-versioner.plist"
DISABLED = PLIST.parent / "_disabled"
LABEL = "com.scg.py-versioner"
LOG_OUT = STATE / "watch.stdout.log"
LOG_ERR = STATE / "watch.stderr.log"
EXTS = {".py", ".sh"}
VER_RE = re.compile(r"^(?P<base>.+?)\s*[Vv](?P<n>\d+)$")
PY = sys.executable
SELF = str(Path(__file__).resolve())


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sha(p):
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_version(p):
    m = VER_RE.match(Path(p).stem)
    return (m.group("base").strip(), int(m.group("n"))) if m else None


def hidden(p):
    n = Path(p).name
    return n.startswith(".") or n.startswith("~")


def mstate(state):
    Path(state).mkdir(parents=True, exist_ok=True)
    return Path(state) / "manifest.json", Path(state) / "versions.jsonl"


def read_manifest(state):
    mp, _ = mstate(state)
    if not mp.is_file():
        return {"schema": 1, "scripts": {}}
    try:
        d = json.loads(mp.read_text(encoding="utf-8"))
        if not isinstance(d.get("scripts"), dict):
            raise ValueError("bad manifest")
        return d
    except Exception:
        return {"schema": 1, "scripts": {}, "recovered": True}


def write_manifest(state, d):
    mp, _ = mstate(state)
    tmp = mp.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(mp))


def ledger(state, rec):
    _, lp = mstate(state)
    with lp.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), **rec}, ensure_ascii=False) + "\n")


def versions(lib, base, ext):
    out = {}
    for p in Path(lib).iterdir():
        if not p.is_file() or hidden(p) or p.suffix.lower() != ext.lower():
            continue
        v = parse_version(p)
        if v and v[0] == base:
            out[v[1]] = p
    return out


def snapshot(path, lib=LIB, state=STATE, note="", actor="manual"):
    """把活文件存为下一个 V 版本。内容 hash 判重是唯一真值，不用 mtime。"""
    p = Path(path)
    r = {"file": str(p), "action": "skipped", "actor": actor}
    if not p.is_file():
        r["reason"] = "not_file"
        return r
    if p.suffix.lower() not in EXTS:
        r["reason"] = "not_script"
        return r
    if hidden(p):
        r["reason"] = "hidden_or_temp"
        return r
    if parse_version(p):
        r["reason"] = "version_snapshot_itself"   # 防「快照触发快照」无限循环
        return r
    base, ext = p.stem, p.suffix
    h = sha(p)
    vs = versions(lib, base, ext)
    latest_n = max(vs) if vs else 0
    if latest_n:
        if sha(vs[latest_n]) == h:
            r.update(action="unchanged", version=latest_n, sha256=h,
                     reason="content_identical_to_latest")
            return r
    n = latest_n + 1
    target = Path(lib) / ("%s V%d%s" % (base, n, ext))
    while target.exists():            # 半途残留 / 并发，绝不覆盖
        n += 1
        target = Path(lib) / ("%s V%d%s" % (base, n, ext))
    prev_hash = {k: sha(q) for k, q in vs.items()}   # 写之前取旧版真实基线，供事后比对
    mstate(state)
    tmp = Path(state) / (".tmp-%d-%s%s" % (os.getpid(), re.sub(r"\W+", "_", base), ext))
    tmp.write_bytes(p.read_bytes())
    os.replace(str(tmp), str(target))
    try:
        shutil.copymode(str(p), str(target))
    except Exception:
        pass
    # 业务产物校验：不看退出码，看文件真的存在 / hash 真的相同 / 号真的递增 / 旧版没被覆盖
    old_intact = all(q.is_file() and sha(q) == prev_hash[k] for k, q in vs.items())
    ok = (target.is_file() and sha(target) == h and n > latest_n and old_intact)
    d = read_manifest(state)
    entry = d["scripts"].setdefault(base, {"versions": []})
    rec = {"version": n, "file": target.name, "sha256": h, "bytes": target.stat().st_size,
           "ts": now(), "actor": actor, "note": note, "verified": bool(ok)}
    entry["versions"] = [x for x in entry.get("versions", []) if x.get("version") != n] + [rec]
    entry["latest"] = n
    entry["live_file"] = p.name
    write_manifest(state, d)
    ledger(state, {"action": "created" if ok else "created_unverified",
                   "script": base, "version": n, "file": target.name,
                   "sha256": h, "actor": actor, "note": note, "verified": bool(ok)})
    r.update(action="created" if ok else "failed", version=n, sha256=h,
             snapshot=target.name, verified=bool(ok), bytes=target.stat().st_size,
             reason="pass" if ok else "business_artifact_check_failed", note=note)
    return r


def freeze(path, lib=LIB, state=STATE, note="", actor="freeze"):
    """固化：语法预检 → 快照入库 → 回读校验。

    用户 2026-11-13 定：「每次脚本的更新都先固化到脚本库里面，部署落地为那个文件。」
    所以固化只接受活文件（名称.py）：活文件既是唯一真源，也是唯一的部署落地对象。
    带 V 号的快照是历史存档，不许当部署目标，也不许手工制造（见踩坑记录）。
    """
    p = Path(path).expanduser()
    if not p.is_absolute() and not p.is_file():
        p = Path(lib) / p            # 允许只给文件名，自动在库内定位
    checks = {}
    if parse_version(p):
        return {"ok": False, "reason": "not_live_file", "file": str(p),
                "hint": "固化只接受活文件（无 V 号）；版本快照由管家自动生成，禁止手工 cp 造号"}
    if not p.is_file():
        return {"ok": False, "reason": "not_file", "file": str(p), "checks": checks}
    # 1) 语法预检：语法都不过就不许入库，避免把坏内容固化成「正式版本」
    if p.suffix.lower() == ".py":
        r = subprocess.run([PY, "-m", "py_compile", str(p)],
                           capture_output=True, text=True, timeout=120)
        checks["syntax"] = (r.returncode == 0)
        if r.returncode != 0:
            return {"ok": False, "reason": "syntax_error", "file": str(p),
                    "stderr": (r.stderr or "").strip()[-500:], "checks": checks}
    else:
        checks["syntax"] = True
    # 2) 快照入库（sha256 判重，内容没变返回 unchanged，不造假版本）
    snap = snapshot(p, lib, state, note, actor)
    checks["snapshot"] = snap.get("action") in ("created", "unchanged")
    checks["snapshot_verified"] = snap.get("action") != "failed"
    # 3) 回读：活文件 hash 必须等于最新版 hash，否则「已固化」是谎报
    vs = versions(lib, p.stem, p.suffix)
    latest_n = max(vs) if vs else 0
    in_sync = bool(latest_n) and sha(vs[latest_n]) == sha(p)
    checks["in_sync"] = in_sync
    ok = all(checks.values())
    return {"ok": ok, "file": str(p), "live_file": p.name,
            "action": snap.get("action"), "version": latest_n,
            "snapshot": snap.get("snapshot"), "live_sha256": sha(p)[:16],
            "deploy_target": str(Path(lib) / p.name),
            "checks": checks, "note": note,
            "reason": "pass" if ok else "verify_failed"}


def reconcile(state, lib=LIB):
    """把磁盘上已存在的版本快照（含历史小写 v1）登记进 manifest，不改动任何文件。"""
    d = read_manifest(state)
    n_added = 0
    for p in sorted(Path(lib).iterdir()):
        if not p.is_file() or hidden(p) or p.suffix.lower() not in EXTS:
            continue
        v = parse_version(p)
        if not v:
            continue
        base, n = v
        e = d["scripts"].setdefault(base, {"versions": []})
        if any(x.get("version") == n for x in e.get("versions", [])):
            continue
        e["versions"] = e.get("versions", []) + [
            {"version": n, "file": p.name, "sha256": sha(p), "bytes": p.stat().st_size,
             "ts": now(), "actor": "reconcile", "note": "磁盘既有版本，登记不改动"}]
        e["latest"] = max([x["version"] for x in e["versions"]])
        n_added += 1
    write_manifest(state, d)
    return n_added


def live_files(lib=LIB):
    for p in sorted(Path(lib).iterdir()):
        if p.is_file() and not hidden(p) and p.suffix.lower() in EXTS and not parse_version(p):
            yield p


def resolve_latest(key="", lib=LIB, state=STATE):
    """【开工门】执行任务前先问一句：这个脚本现在该跑哪个文件？

    用户 2026-11-13 定：跨任务全局策略 —— 动手前先检测脚本库里的最新版。
    判据（缺一条都不算通过，不看退出码）：
      1. 活文件（无 V 号）必须存在，且 .py 能编译；
      2. 活文件 sha256 == 最高 V 快照 sha256（不同 = 有未固化改动，先 freeze 再跑）；
      3. 最高 V 号与 manifest.latest 一致（账本不撒谎）；
    不通过时 run 仍是「当前最合理的那份」，但 ok=False、needs_freeze=True。
    """
    lib = Path(lib)
    idx = {}
    for p in sorted(lib.iterdir()):
        if not p.is_file() or hidden(p) or p.suffix.lower() not in EXTS:
            continue
        v = parse_version(p)
        if v:
            idx.setdefault((v[0], p.suffix.lower()), {})[v[1]] = p
        else:
            idx.setdefault((p.stem, p.suffix.lower()), {})["liv"] = p
    man = read_manifest(state).get("scripts", {})
    q = (key or "").strip().lower()
    out = []
    for (base, ext), d in sorted(idx.items()):
        if q and q not in base.lower():
            continue
        live = d.get("liv")
        snaps = {k: v for k, v in d.items() if isinstance(k, int)}
        top = max(snaps) if snaps else 0
        r = {"base": base, "ext": ext, "live": str(live) if live else None,
             "latest_version": top, "snapshot": str(snaps[top]) if top else None}
        checks = {}
        if live and top:
            checks["live_matches_top"] = sha(live) == sha(snaps[top])
        if live:
            checks["live_present"] = True
            if ext == ".py":
                try:
                    compile(live.read_text(encoding="utf-8"), str(live), "exec")
                    checks["syntax_ok"] = True
                except Exception as e:
                    checks["syntax_ok"] = False
                    r["syntax_error"] = str(e)[:200]
        if top:
            checks["ledger_latest"] = (man.get(base, {}).get("latest") == top)
        r["checks"] = checks
        if live and not top:
            gate, run, need = "never_frozen", live, True
        elif live and top and checks.get("live_matches_top") and checks.get("ledger_latest") is False:
            gate, run, need = "ledger_lag", live, True   # 内容对得上但账本没记，先 init/reconcile
        elif live and top and checks.get("live_matches_top"):
            gate, run, need = "in_sync", live, False
        elif live and top:
            gate, run, need = "dirty_live", live, True
        elif top:
            gate, run, need = "snapshot_only", snaps[top], False
        else:
            gate, run, need = "missing", None, True
        if not checks.get("syntax_ok", True):
            need = True
        r.update({"gate": gate, "run": str(run) if run else None, "needs_freeze": need,
                  "ok": gate in ("in_sync", "snapshot_only") and not need})
        out.append(r)
    ok = bool(out) and all(x["ok"] for x in out)
    return {"ts": now(), "query": key or "*", "count": len(out), "ok": ok,
            "need_freeze": [x["base"] for x in out if x["needs_freeze"]], "items": out}


def scan(state=STATE, lib=LIB, actor="scan"):
    rs = [snapshot(p, lib, state, actor=actor) for p in live_files(lib)]
    return {"ts": now(), "lib": str(lib), "scanned": len(rs),
            "created": [r for r in rs if r["action"] == "created"],
            "unchanged": [r for r in rs if r["action"] == "unchanged"],
            "results": rs}


def watch(state=STATE, lib=LIB):
    fw = shutil.which("fswatch") or "${FSWATCH}"
    if not Path(fw).is_file():
        print(json.dumps({"error": "fswatch not found", "path": fw}, ensure_ascii=False), flush=True)
        return 2
    cmd = [fw, "-o", "-l", "1.5",
           "-e", r"/\.",                                  # 隐藏文件 / .git
           "-e", r"\.DS_Store",
           "-e", r"[[:space:]]*[Vv][0-9]+\.(py|sh)$",    # 版本快照自身，防自触发
           "-e", r"\.tmp-",
           str(lib)]
    # 注意：fswatch 1.22.0 用 POSIX ERE，不认 \s / \d；用 \s*[Vv]\d+ 会直接
    # "error during compilation" rc=5 退出，曾导致 KeepAlive 每 10 秒重启空转守护。
    mstate(state)
    # stderr 继承父进程（launchd 的 watch.stderr.log）：用 PIPE 会吞掉 fswatch 的
    # 正则编译错误，把「守护没在真跑」伪装成「正常退出」（成功日志不可证伪）。
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=None,
                            text=True, bufsize=1)
    print(json.dumps({"event": "watch_started", "ts": now(), "pid": os.getpid(),
                      "fswatch": fw, "lib": str(lib), "cmd": cmd}, ensure_ascii=False), flush=True)
    try:
        for line in proc.stdout:
            if not line.strip():
                continue
            r = scan(state, lib, actor="watch")
            created = [{"script": x["file"], "version": x["version"], "sha256": x["sha256"]}
                       for x in r["created"]]
            print(json.dumps({"ts": now(), "batch": line.strip(), "scanned": r["scanned"],
                              "created": created, "unchanged": len(r["unchanged"])},
                             ensure_ascii=False), flush=True)
            ledger(state, {"action": "watch_batch", "batch": line.strip(),
                           "created": len(created), "scanned": r["scanned"]})
    except KeyboardInterrupt:
        pass
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
    rc = proc.poll()
    if rc not in (0, None):
        print(json.dumps({"event": "fswatch_exited", "ts": now(), "rc": rc,
                          "note": "非零 rc 说明守护没在真跑，详见 watch.stderr.log"},
                         ensure_ascii=False), flush=True)
        ledger(state, {"action": "fswatch_exited", "rc": rc})
        return 3
    return 0


PLIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{py}</string>
        <string>{self_path}</string>
        <string>watch</string>
    </array>
    <key>WorkingDirectory</key><string>{lib}</string>
    <key>KeepAlive</key><true/>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>{out}</string>
    <key>StandardErrorPath</key><string>{err}</string>
</dict>
</plist>
"""


def service_loaded():
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=12)
        return LABEL in (r.stdout or "")
    except Exception:
        return False


def install_service(state=STATE, lib=LIB):
    mstate(state)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    xml = PLIST_XML.format(label=LABEL, py=PY, self_path=SELF, lib=str(lib),
                           out=str(LOG_OUT), err=str(LOG_ERR))
    PLIST.write_text(xml, encoding="utf-8")
    acts = []
    r0 = subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True, text=True, timeout=15)
    acts.append("unload_old rc=%s" % r0.returncode)
    r1 = subprocess.run(["launchctl", "load", str(PLIST)], capture_output=True, text=True, timeout=15)
    acts.append("load rc=%s %s" % (r1.returncode, (r1.stderr or "").strip()[:120]))
    loaded = service_loaded()
    if not loaded:
        uid = os.getuid()
        r2 = subprocess.run(["launchctl", "bootstrap", "gui/%d" % uid, str(PLIST)],
                            capture_output=True, text=True, timeout=15)
        acts.append("bootstrap rc=%s %s" % (r2.returncode, (r2.stderr or "").strip()[:120]))
        loaded = service_loaded()
    return {"plist": str(PLIST), "label": LABEL, "actions": acts,
            "service_loaded": loaded, "python": PY, "script": SELF,
            "watch_log": str(LOG_OUT),
            "note": "KeepAlive+RunAtLoad；调度源已用 launchctl list 实证，非注释自称"}


def uninstall_service():
    acts = []
    if PLIST.is_file():
        r = subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True, text=True, timeout=15)
        acts.append("unload rc=%s" % r.returncode)
        DISABLED.mkdir(parents=True, exist_ok=True)
        dst = DISABLED / (PLIST.name + "." + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        PLIST.rename(dst)
        acts.append("plist_moved_to=" + str(dst))
    else:
        acts.append("plist_absent")
    return {"actions": acts, "service_loaded": service_loaded(),
            "note": "plist 只移入 _disabled，不删除，可原样放回恢复"}


def status(state=STATE, lib=LIB):
    d = read_manifest(state)
    scripts = {}
    for p in live_files(lib):
        h = sha(p)
        vs = versions(lib, p.stem, p.suffix)
        latest_n = max(vs) if vs else 0
        in_sync = bool(latest_n and sha(vs[latest_n]) == h)
        scripts[p.stem] = {"live_file": p.name, "live_sha256": h,
                           "latest_version": latest_n, "in_sync": in_sync,
                           "versions": {str(k): v.name for k, v in sorted(vs.items())}}
    for p in sorted(Path(lib).iterdir()):
        if not p.is_file() or hidden(p) or p.suffix.lower() not in EXTS:
            continue
        v = parse_version(p)
        if v and v[0] not in scripts:
            vs = versions(lib, v[0], p.suffix)
            scripts[v[0]] = {"live_file": None, "latest_version": max(vs) if vs else 0,
                             "in_sync": None,
                             "versions": {str(k): q.name for k, q in sorted(vs.items())}}
    fswatch_alive = False
    try:
        r = subprocess.run(["pgrep", "-f", "fswatch.*Documents/py"], capture_output=True,
                           text=True, timeout=8)
        fswatch_alive = bool((r.stdout or "").strip())
    except Exception:
        pass
    # 重启风暴检测：只「服务在」不够，KeepAlive 反复拉起同样是静默失败
    restarts_5min = 0
    try:
        if LOG_OUT.is_file():
            cutoff = dt.datetime.now().astimezone() - dt.timedelta(minutes=5)
            for line in LOG_OUT.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]:
                try:
                    j = json.loads(line)
                    if (j.get("event") == "watch_started"
                            and dt.datetime.fromisoformat(j["ts"]) > cutoff):
                        restarts_5min += 1
                except Exception:
                    pass
    except Exception:
        pass
    _, lp = mstate(state)
    ledger_last = None
    try:
        lines = lp.read_text(encoding="utf-8").splitlines()
        ledger_last = json.loads(lines[-1]).get("ts") if lines else None
    except Exception:
        pass
    loaded = service_loaded()
    warnings = []
    if not loaded:
        warnings.append("launchd 未加载（launchctl list 无 %s）" % LABEL)
    if not fswatch_alive:
        warnings.append("fswatch 进程未发现（watch 未运行）")
    if restarts_5min > 2:
        warnings.append("watch 5 分钟内重启 %d 次（守护反复退出，查 watch.stderr.log）" % restarts_5min)
    for name, info in scripts.items():
        if info["live_file"] and info["in_sync"] is False:
            warnings.append("未归档改动: " + name)
    return {"checked_at": now(), "lib": str(lib), "state": str(state),
            "service_loaded": loaded, "fswatch_alive": fswatch_alive,
            "watch_restarts_5min": restarts_5min,
            "plist_exists": PLIST.is_file(), "watch_log": str(LOG_OUT),
            "ledger_last": ledger_last,
            "manifest_scripts": len(d.get("scripts", {})),
            "live_scripts": sum(1 for x in scripts.values() if x["live_file"]),
            "total_versions": sum(len(x["versions"]) for x in scripts.values()),
            "scripts": scripts, "warnings": warnings,
            "verdict": "READY" if not warnings else "DEGRADED"}


def selftest():
    import tempfile
    checks = []

    def ck(name, cond, detail=""):
        checks.append((name, bool(cond), str(detail)[:120]))

    with tempfile.TemporaryDirectory(prefix="pyver-selftest-") as td:
        lib = Path(td) / "lib"
        state = Path(td) / "state"
        lib.mkdir()
        f = lib / "示例脚本.py"
        f.write_text("# V1 content\n", encoding="utf-8")
        r1 = snapshot(f, lib, state, actor="selftest")
        ck("V1 基线生成", r1["action"] == "created" and r1["version"] == 1,
           r1.get("snapshot"))
        v1 = lib / "示例脚本 V1.py"
        ck("V1 文件真实存在且 hash 一致", v1.is_file() and sha(v1) == sha(f))
        # mtime 变了但内容没变 —— 不许产生假 V2
        os.utime(f, (time.time() + 500, time.time() + 500))
        r2 = snapshot(f, lib, state, actor="selftest")
        ck("mtime 变更不产生假版本", r2["action"] == "unchanged" and r2["version"] == 1,
           r2.get("reason"))
        f.write_text("# V2 content\n", encoding="utf-8")
        r3 = snapshot(f, lib, state, actor="selftest")
        ck("内容变更生成 V2", r3["action"] == "created" and r3["version"] == 2,
           r3.get("snapshot"))
        v2 = lib / "示例脚本 V2.py"
        ck("V2 hash 与活文件一致", v2.is_file() and sha(v2) == sha(f))
        ck("V1 未被覆盖", v1.read_text(encoding="utf-8") == "# V1 content\n")
        ck("V1/V2 hash 不同", sha(v1) != sha(v2))
        # freeze：固化只认活文件，内容未变返回 unchanged 且 ok
        fr = freeze(f, lib, state, note="selftest", actor="selftest")
        ck("freeze 活文件通过且不造假版本",
           fr["ok"] and fr["action"] == "unchanged" and fr["version"] == 2,
           "action=%s v=%s" % (fr.get("action"), fr.get("version")))
        ck("freeze 回读 in_sync", fr["checks"].get("in_sync") is True)
        fv = freeze(v2, lib, state, actor="selftest")
        ck("freeze 拒绝版本快照当部署目标",
           (not fv["ok"]) and fv["reason"] == "not_live_file", fv.get("reason"))
        bad = Path(td) / "坏脚本.py"        # 放在库外，避免被后续 scan 收进账本
        bad.write_text("def f(:\n", encoding="utf-8")
        fb = freeze(bad, lib, state, actor="selftest")
        ck("freeze 语法错不入库",
           (not fb["ok"]) and fb["reason"] == "syntax_error"
           and not (lib / "坏脚本 V1.py").exists(), fb.get("reason"))
        # 快照自身触发 → 跳过（防无限循环）
        r4 = snapshot(v2, lib, state, actor="selftest")
        ck("版本快照自身被跳过", r4["action"] == "skipped" and
           r4["reason"] == "version_snapshot_itself", r4.get("reason"))
        sc = scan(state, lib, actor="selftest")
        ck("重复 scan 不生成 V3", not any(x["action"] == "created" for x in sc["results"]) and
           not (lib / "示例脚本 V3.py").exists())
        d = read_manifest(state)
        ck("manifest latest=2", d["scripts"]["示例脚本"]["latest"] == 2)
        # latest：开工门——in_sync / dirty_live / never_frozen 三态
        rl = resolve_latest("示例脚本", lib, state)
        ck("latest in_sync 时 ok 且 run=活文件",
           rl["ok"] and rl["items"][0]["gate"] == "in_sync"
           and rl["items"][0]["run"] == str(f), rl["items"][0].get("gate"))
        f.write_text("# V3 dirty\n", encoding="utf-8")
        rd = resolve_latest("示例脚本", lib, state)
        ck("latest 检出未固化改动",
           (not rd["ok"]) and rd["items"][0]["gate"] == "dirty_live"
           and rd["items"][0]["needs_freeze"] and rd["need_freeze"] == ["示例脚本"],
           rd["items"][0].get("gate"))
        f.write_text("# V2 content\n", encoding="utf-8")   # 复原，避免污染后续 scan 断言
        nf = Path(td) / "lib2"
        nf.mkdir(exist_ok=True)
        (nf / "孤立活文件.py").write_text("# solo\n", encoding="utf-8")
        rn = resolve_latest("孤立活文件", nf, state)
        ck("latest 检出从未固化的新脚本",
           rn["items"][0]["gate"] == "never_frozen" and rn["items"][0]["needs_freeze"],
           rn["items"][0].get("gate"))
        rs0 = resolve_latest("不存在的脚本名", lib, state)
        ck("latest 空命中不假绿", rs0["count"] == 0 and rs0["ok"] is False)
        d2 = read_manifest(state)
        keep = d2["scripts"]["示例脚本"]["latest"]
        d2["scripts"]["示例脚本"]["latest"] = 1          # 人为让账本滞后
        write_manifest(state, d2)
        rg = resolve_latest("示例脚本", lib, state)
        ck("latest 检出账本滞后",
           rg["items"][0]["gate"] == "ledger_lag" and rg["items"][0]["needs_freeze"],
           rg["items"][0].get("gate"))
        d2 = read_manifest(state)
        d2["scripts"]["示例脚本"]["latest"] = keep
        write_manifest(state, d2)
        _, lp = mstate(state)
        recs = [json.loads(x) for x in lp.read_text(encoding="utf-8").splitlines()]
        ck("账本 created 恰好 2 条",
           sum(1 for x in recs if x["action"] == "created") == 2, "ledger=%d" % len(recs))
        ck("成功判据含业务产物", all(x.get("verified") for x in recs if x["action"] == "created"))
        txt = lib / "notes.txt"
        txt.write_text("not a script", encoding="utf-8")
        ck("非脚本扩展名跳过", snapshot(txt, lib, state)["reason"] == "not_script")
        hid = lib / ".hidden.py"
        hid.write_text("x", encoding="utf-8")
        ck("隐藏/临时文件跳过", snapshot(hid, lib, state)["reason"] == "hidden_or_temp")
        n = reconcile(state, lib)
        ck("reconcile 登记既有版本", n == 0, "added=%d" % n)
    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print("%s %-28s %s" % ("PASS" if ok else "FAIL", name, detail))
    print("---- %d/%d ----" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description="脚本版本管家 · 每次更改自动存为新版本")
    ap.add_argument("cmd", nargs="?", default="status",
                    choices=["init", "freeze", "save", "scan", "watch", "list", "status",
                             "latest", "install-service", "uninstall-service"])
    ap.add_argument("file", nargs="?", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="latest 专用：只认 in_sync，其余一律 exit 1（给 automation 当前置卡口）")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.cmd == "init":
        n = reconcile(STATE, LIB)
        r = scan(STATE, LIB, actor="init")
        print(json.dumps({"reconciled": n, **r}, ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "freeze":
        if not a.file:
            print("freeze 需要 <file>（活文件名或路径）")
            return 2
        r = freeze(a.file, LIB, STATE, a.note, "freeze")
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    if a.cmd == "save":
        if not a.file:
            print("save 需要 <file> 路径")
            return 2
        print(json.dumps(snapshot(Path(a.file).expanduser(), LIB, STATE, a.note, "save"),
                        ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "scan":
        print(json.dumps(scan(STATE, LIB), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "watch":
        return watch(STATE, LIB)
    if a.cmd == "list":
        if not a.name:
            print(json.dumps(read_manifest(STATE), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(read_manifest(STATE)["scripts"].get(a.name, {"error": "not found"}),
                            ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "latest":
        r = resolve_latest(a.name or a.file, LIB, STATE)
        if a.strict:
            r["strict"] = True
            r["ok"] = bool(r["items"]) and all(x["gate"] == "in_sync" for x in r["items"])
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1
    if a.cmd == "status":
        print(json.dumps(status(STATE, LIB), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "install-service":
        print(json.dumps(install_service(STATE, LIB), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "uninstall-service":
        print(json.dumps(uninstall_service(), ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
