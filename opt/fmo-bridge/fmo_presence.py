# -*- coding: utf-8 -*-
"""FMO 在线宣告（presence）——真实设备靠这些报文感知"谁在线"。

实测报文（来自 EMQX trace 里的真实设备 BG7OYZ / BG8LKV / BG2CPU）：

  FMO/TELE （33 字节，约每 30 秒一次）
    偏移 大小 字段
    0    1   version = 2
    1    4   session（设备内会话值）
    5    4   UID（小端）
    9    12  呼号（不足补 0）
    21   4   UTC 秒（小端）
    25   4   发射频率 MHz（float32 小端）
    29   4   接收频率 MHz（float32 小端）

  FMO/LATE/UID_V1/<uid> （9 字节，延迟上报）
    0    4   延迟毫秒（小端）
    4    4   （实测为变化值）
    8    1   （实测为变化值）

真实设备上报频率后，其他设备才知道"这台在哪个频点"，因此桥接也必须上报
自己的收发频率（对应热点 ch5 的 TX/RX）。
"""
import struct
import time

TELE_VERSION = 2


def build_tele(uid, callsign, tx_mhz=0.0, rx_mhz=0.0, session=None):
    """构造 FMO/TELE 遥测/在线宣告报文（33 字节）。"""
    if session is None:
        session = int(time.time() * 1000) & 0xFFFFFFFF
    b = bytearray()
    b += bytes([TELE_VERSION])
    b += struct.pack("<I", int(session) & 0xFFFFFFFF)
    b += struct.pack("<I", int(uid) & 0xFFFFFFFF)
    b += str(callsign).upper().encode("ascii")[:12].ljust(12, b"\x00")
    b += struct.pack("<I", int(time.time()) & 0xFFFFFFFF)
    b += struct.pack("<f", float(tx_mhz))
    b += struct.pack("<f", float(rx_mhz))
    return bytes(b)


def build_late(latency_ms=0, seq=0, tail=0):
    """构造 FMO/LATE/UID_V1/<uid> 延迟上报（9 字节）。"""
    return (struct.pack("<I", int(latency_ms) & 0xFFFFFFFF)
            + struct.pack("<I", int(seq) & 0xFFFFFFFF)
            + bytes([int(tail) & 0xFF]))


# 真实设备上线时订阅的主题（取自 trace 里 BG8LKV 的 SUBSCRIBE 序列 + roles/user.json）
USER_SUBSCRIPTIONS = [
    "ARCH/HRC",
    "FMO/TELE",
    "FMO/USER_RIG",
    "FMO/PROFILE",
    "FMO/SERVER_INFO",
    "FMO/QSO/+",
    "FMO/LATE/UID_V1/+",
]
