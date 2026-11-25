#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ARCH 硬件通道上限：旧树莓派和低资源设备自动限流。"""
import json
import os
import sys

MODEL_FILES = ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model")


def read_model():
    for p in MODEL_FILES:
        try:
            with open(p, "rb") as f:
                return f.read().replace(b"\x00", b"").decode("utf-8", "ignore").strip()
        except OSError:
            pass
    return ""


def memory_mb():
    try:
        for line in open("/proc/meminfo", encoding="ascii", errors="ignore"):
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def decision(model=None):
    model = (model if model is not None else read_model()).strip()
    cores = os.cpu_count() or 1
    mem = memory_mb()
    # User-controlled policy: no hardware-based hard cap.  The software model
    # supports 11 channel slots; whether the device can carry them is the
    # operator's decision.
    return {"max_channels": 11, "profile": "用户自定义（无硬件限制）",
            "reason": "软件支持最多 11 路；是否启用由用户决定",
            "model": model or "未知设备", "memory_mb": mem, "cpu_cores": cores}


def main():
    d = decision()
    if "--json" in sys.argv[1:]:
        print(json.dumps(d, ensure_ascii=False))
    else:
        print(d["max_channels"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())