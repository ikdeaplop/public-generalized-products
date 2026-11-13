#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
紧急止血器：吞掉恶意/异常的按键注入事件。
默认拦截 keycode 50（Grave，中文输入法下输出「·」间隔号）。
需要「辅助功能」权限才能创建过滤型 EventTap。
用法：
    python3 key-blocker.py 50 47     # 拦截指定的 keycode
停止：pkill -f key-blocker.py
"""
import sys, time, signal
import Quartz

CODES = set(int(x) for x in (sys.argv[1:] or ["50"]))
blocked = [0]
passed = [0]
T0 = time.time()


def cb(proxy, etype, event, refcon):
    if etype in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
        kc = Quartz.CGEventGetIntegerValueField(event, 9)
        if kc in CODES:
            blocked[0] += 1
            return None          # 返回 None = 丢弃该事件
    passed[0] += 1
    return event


def main():
    mask = ((1 << Quartz.kCGEventKeyDown) | (1 << Quartz.kCGEventKeyUp))
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGHIDEventTap, Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionDefault,   # 过滤型：可丢弃事件
        mask, cb, None)
    if not tap:
        print("!! 无权限：需要在「系统设置 > 隐私与安全性 > 辅助功能」中授权当前 Python",
              flush=True)
        sys.exit(2)

    src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src,
                              Quartz.kCFRunLoopDefaultMode)
    Quartz.CGEventTapEnable(tap, True)
    print("止血器运行中：拦截 keycode %s ｜ 停止命令 pkill -f key-blocker.py"
          % sorted(CODES), flush=True)

    def report(signum, frame):
        print("统计：已拦截 %d 次，放行 %d 次，运行 %.0f 秒"
              % (blocked[0], passed[0], time.time() - T0), flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, report)
    signal.signal(signal.SIGINT, report)

    nxt = time.time() + 10
    while True:
        Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.5, False)
        if time.time() >= nxt:
            print("[%.0fs] 已拦截 %d 次异常按键" % (time.time() - T0, blocked[0]),
                  flush=True)
            nxt = time.time() + 10


if __name__ == "__main__":
    main()
