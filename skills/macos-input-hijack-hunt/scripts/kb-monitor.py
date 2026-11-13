#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓 8 秒内的键盘/鼠标事件，打印来源进程 PID —— 用于排查"谁在动我的键盘鼠标" """
import sys, time
import Quartz
from Quartz import (
    CGEventTapCreate, CGEventTapEnable, CFRunLoopRunInMode,
    kCGEventTapOptionListenOnly, kCGHIDEventTap, kCGHeadInsertEventTap,
    kCFRunLoopDefaultMode, CGEventGetIntegerValueField, CGEventGetType,
)
from Quartz import kCGKeyboardEventKeycode  # noqa

PID_FIELD = 0x3B  # kCGEventSourceUnixProcessID ≈ 59

NAMES = {
    Quartz.kCGEventKeyDown: "KeyDown",
    Quartz.kCGEventKeyUp: "KeyUp",
    Quartz.kCGEventFlagsChanged: "Flags",
    Quartz.kCGEventMouseMoved: "MouseMoved",
    Quartz.kCGEventLeftMouseDown: "LMB-Down",
    Quartz.kCGEventLeftMouseUp: "LMB-Up",
    Quartz.kCGEventRightMouseDown: "RMB-Down",
    Quartz.kCGEventScrollWheel: "Scroll",
}

COUNT = {}

def cb(proxy, etype, event, refcon):
    t = CGEventGetType(event)
    name = NAMES.get(t, "type=%d" % t)
    pid = CGEventGetIntegerValueField(event, PID_FIELD)
    keycode = ""
    if t in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
        keycode = " kc=%s" % CGEventGetIntegerValueField(event, 9)  # kCGKeyboardEventKeycode
    loc = ""
    if t in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDown,
             Quartz.kCGEventRightMouseDown):
        p = Quartz.CGEventGetLocation(event)
        loc = " @(%d,%d)" % (int(p.x), int(p.y))
    print("%.2fs  %-12s pid=%-7s%s%s" % (time.time() - T0, name, pid, keycode, loc),
          flush=True)
    COUNT[name] = COUNT.get(name, 0) + 1
    return event

T0 = time.time()
tap = CGEventTapCreate(
    kCGHIDEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly,
    (1 << Quartz.kCGEventKeyDown) | (1 << Quartz.kCGEventKeyUp)
    | (1 << Quartz.kCGEventMouseMoved) | (1 << Quartz.kCGEventLeftMouseDown)
    | (1 << Quartz.kCGEventLeftMouseUp) | (1 << Quartz.kCGEventRightMouseDown)
    | (1 << Quartz.kCGEventScrollWheel),
    cb, None)

if not tap:
    print("!! 无法创建事件监听（需要给该 Python 授予「辅助功能」权限）", flush=True)
    sys.exit(2)

src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src, kCFRunLoopDefaultMode)
CGEventTapEnable(tap, True)
print("=== 开始监听 8 秒，请不要碰键盘鼠标 ===", flush=True)

end = time.time() + 8
while time.time() < end:
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.25, False)

print("=== 监听结束，事件统计：%s ===" % (COUNT or "（完全没有任何输入事件）"), flush=True)
