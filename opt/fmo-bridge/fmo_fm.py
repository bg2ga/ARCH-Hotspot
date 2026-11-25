# -*- coding: utf-8 -*-
"""MMDVM-Host FM Network（g4klx CFMNetwork）UDP 收发。

线协议（见 MMDVM-Host/FMNetwork.cpp）：
  FMS<callsign>\\0   开始发射
  FMD<s16le ...>     音频数据（8000Hz / 16bit 有符号 / 单声道）
  FME                结束
  FMP                心跳（每 5s）

本桥接程序扮演 Host 配置里 [FM Network] 的 Gateway 角色。
"""
import socket
import struct
import threading
import time

FS_START = b"FMS"
FS_DATA = b"FMD"
FS_END = b"FME"
FS_PING = b"FMP"

LOCAL_CALLSIGN = "N0CALL"


class FmNetwork:
    """在 GatewayAddress:GatewayPort 上收 Host 的 FM 音频，并可反向回送。"""

    def __init__(self, bind_addr, bind_port, callsign=LOCAL_CALLSIGN,
                 on_start=None, on_audio=None, on_end=None, debug=False):
        self.bind = (bind_addr, int(bind_port))
        self.callsign = callsign.upper()
        self.debug = debug
        self.on_start = on_start
        self.on_audio = on_audio
        self.on_end = on_end
        self.peer = None                 # 最近一次发包的 Host 地址
        self.seq = 0
        self.tx_active = False
        self._sock = None
        self._stop = False
        self._last_rx = 0.0
        self._ping_t = 0.0

    # ---------------------------------------------------------- 生命周期
    def open(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(self.bind)
        self._sock.settimeout(0.2)
        return self

    def close(self):
        self._stop = True
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def run_forever(self):
        while not self._stop:
            try:
                data, addr = self._sock.recvfrom(2048)
            except socket.timeout:
                self._tick()
                continue
            except OSError:
                break
            self.peer = addr
            self._last_rx = time.time()
            try:
                self._handle(data)
            except Exception as e:
                if self.debug:
                    print("[FM] handle error:", e)
            self._tick()

    def _tick(self):
        now = time.time()
        if now - self._ping_t >= 5.0 and self.peer:
            self._ping_t = now
            self._send(FS_PING)

    def start_thread(self):
        t = threading.Thread(target=self.run_forever, daemon=True)
        t.start()
        return t

    # ---------------------------------------------------------- 接收
    def _handle(self, data):
        if len(data) < 3 or data[0:2] != b"FM":
            return
        tag = data[0:3]
        if self.debug:
            print("[FM] rx %s len=%d" % (tag.decode("ascii", "ignore"), len(data)))
        if tag == FS_DATA:
            pcm = data[3:]
            if self.on_audio:
                self.on_audio(pcm)
        elif tag == FS_START:
            cs = data[3:].split(b"\x00", 1)[0].decode("ascii", "ignore")
            if self.debug:
                print("[FM] START from", cs)
            if self.on_start:
                self.on_start(cs)
        elif tag == FS_END:
            if self.debug:
                print("[FM] END")
            if self.on_end:
                self.on_end()

    # ---------------------------------------------------------- 发送
    def _send(self, blob):
        if self.peer and self._sock:
            try:
                self._sock.sendto(blob, self.peer)
            except OSError:
                pass

    def send_start(self):
        self.seq = 0
        self.tx_active = True
        self._send(FS_START + self.callsign.encode("ascii") + b"\x00")

    def send_audio(self, pcm_bytes):
        if not self.tx_active:
            self.send_start()
        self.seq += 1
        self._send(FS_DATA + pcm_bytes)

    def send_end(self):
        if self.tx_active:
            self.tx_active = False
            self._send(FS_END)

    def send_ping(self):
        self._send(FS_PING)


def pcm_gain(pcm_bytes, gain):
    if gain == 1.0:
        return pcm_bytes
    n = len(pcm_bytes) // 2
    out = bytearray(len(pcm_bytes))
    for i in range(n):
        v = int.from_bytes(pcm_bytes[i * 2:i * 2 + 2], "little", signed=True)
        v = max(-32768, min(32767, int(v * gain)))
        out[i * 2:i * 2 + 2] = int(v).to_bytes(2, "little", signed=True)
    return bytes(out)
