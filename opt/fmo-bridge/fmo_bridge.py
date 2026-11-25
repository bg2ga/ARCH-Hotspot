#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FMO 桥接网关：MMDVM-Host「FM 模式」↔ FMO 4.0 网络（MQTT/EMQX + SAS 证书认证）。

数据流：
  RF 侧 : MMDVM-Host --FM Network(UDP, 8kHz S16LE)--> 本程序 --OPUS/RADPCM--> MQTT FMO/RAW
  网络侧: MQTT FMO/RAW --解包/解码--> 本程序 --FM Network(UDP)--> MMDVM-Host

用法：python3 fmo_bridge.py config.json
"""
import hashlib
import array
import json
import os
import os
import queue
import signal
import struct
import sys
import threading
import time
import wave
from collections import deque

import paho.mqtt.client as mqtt

import fmo_proto as P
import fmo_packet as PK
import fmo_presence as PRES
from fmo_fm import FmNetwork, pcm_gain
from fmo_voice import OpusCodec, RadPcmCodec, OPUS_FRAME, RADPCM_FRAME
from fmo_dsp import DownlinkShaper, make_tone_pcm

RAW_TOPIC = "ARCH/HRC"          # 默认直连全网语音主题；若配置了 voice.topic 则用它
# 上行流的分段判据：RF 音频静默超过这个时间就结束本段流。
# 必须大于正常语音的停顿间隙，否则一句话中间就会重开流 → 对端听感断续；
# PTT 松开由 FME 显式收尾，不依赖这里。
STREAM_IDLE_S = 1.2

def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


class AppPcmRelay:
    TOPIC='FMO/QSO/ARCH-FMO8P'
    OUT_N=320                 # 40 ms @ 8 kHz
    PREBUFFER=3200            # 400 ms；比原来的 800 ms 少一半，端到端留出余量
    MAX_QUEUE=24000           # 3 s 硬上限，防止延迟越积越大
    RATE_MIN=296              # 每 40 ms 最少消费 296 个输入样本（约 -7.5%）
    RATE_MAX=340              # 每 40 ms 最多消费 340 个输入样本（约 +6.25%）
    RATE_SMOOTH=0.08          # 连续调节，禁止逐帧大幅变速造成音质抖动
    def __init__(self,publish_fn):
        self.publish_fn=publish_fn; self.q=deque(); self.q_n=0
        self.cv=threading.Condition(); self.stream=None
        self.uid=0; self.callsign=''; self.sid=0; self.seq=0
        self.last_rx=0.0; self.stop_flag=False
        self.last_frame=array.array('h',[0]*self.OUT_N); self.plc_gain=1.0
        self.rate=float(self.OUT_N); self.underruns=0
        self.stream_frames=0; self.stream_start=0.0
        self.rate_samples=0; self.rate_start=time.monotonic(); self.fixed_rate=False
        self.pending_key=None; self.pending_q=deque(); self.pending_n=0
        self.pending_last=0.0; self.pending_uid=0; self.pending_callsign=''
        self.pending_sid=0; self.pending_frames=0; self.pending_first_len=0
    def start(self): threading.Thread(target=self.run_player,daemon=True,name='app-pcm-relay').start()
    def stop(self):
        with self.cv:
            self.stop_flag=True; self.cv.notify_all()
    def _start_current_locked(self,key,uid,callsign,sid,first_len):
        self.q.clear(); self.q_n=0
        self.stream=key; self.uid=int(uid); self.callsign=str(callsign or 'FMO'); self.sid=int(sid)
        self.seq=0; self.plc_gain=1.0
        self.rate=float(self.RATE_MIN if first_len>=1280 else self.OUT_N)
        self.fixed_rate=(first_len<1280)
        self.underruns=0; self.stream_frames=0; self.stream_start=time.monotonic()
        self.rate_samples=0; self.rate_start=time.monotonic(); self.last_rx=time.monotonic()
    def _drop_pending_locked(self):
        self.pending_key=None; self.pending_q=deque(); self.pending_n=0
        self.pending_last=0.0; self.pending_frames=0; self.pending_first_len=0
    def _adopt_pending_locked(self):
        if self.pending_key is None or self.pending_n<=0: return False
        self.q=self.pending_q; self.q_n=self.pending_n
        self.stream=self.pending_key; self.uid=self.pending_uid; self.callsign=self.pending_callsign or 'FMO'
        self.sid=self.pending_sid; self.seq=0; self.plc_gain=1.0
        self.rate=float(self.RATE_MIN if self.pending_first_len>=1280 else self.OUT_N)
        self.fixed_rate=(self.pending_first_len<1280)
        self.underruns=0; self.stream_frames=self.pending_frames; self.stream_start=time.monotonic()
        self.rate_samples=self.pending_n; self.rate_start=time.monotonic(); self.last_rx=self.pending_last
        self.pending_key=None; self.pending_q=deque(); self.pending_n=0
        self.pending_last=0.0; self.pending_frames=0; self.pending_first_len=0
        self.cv.notify_all(); return True
    def feed(self,pcm,uid,callsign,sid):
        if not pcm: return
        key=(int(sid),int(uid)); a=array.array('h'); a.frombytes(pcm[:len(pcm)//2*2])
        if not a: return
        now=time.monotonic(); activated=False
        with self.cv:
            current_live=(self.stream is not None and self.last_rx>0 and now-self.last_rx<1.2 and self.q_n>0)
            if self.stream is None or self.q_n==0 or not current_live:
                if self.stream!=key: self._start_current_locked(key,uid,callsign,sid,len(a)); activated=True
                if self.stream_frames==0:
                    self.rate=float(self.RATE_MIN if len(a)>=1280 else self.OUT_N); self.fixed_rate=(len(a)<1280)
                self.q.append(a); self.q_n+=len(a); self.rate_samples+=len(a); self.stream_frames+=1; self.last_rx=now
            elif key==self.stream:
                if self.stream_frames==0:
                    self.rate=float(self.RATE_MIN if len(a)>=1280 else self.OUT_N); self.fixed_rate=(len(a)<1280)
                self.q.append(a); self.q_n+=len(a); self.rate_samples+=len(a); self.stream_frames+=1; self.last_rx=now
            else:
                if key!=self.pending_key or (self.pending_last>0 and now-self.pending_last>0.35):
                    self._drop_pending_locked(); self.pending_key=key; self.pending_uid=int(uid)
                    self.pending_callsign=str(callsign or 'FMO'); self.pending_sid=int(sid); self.pending_first_len=len(a)
                self.pending_q.append(a); self.pending_n+=len(a); self.pending_frames+=1; self.pending_last=now
                if self.pending_n>=self.PREBUFFER: self._adopt_pending_locked(); activated=True
            while self.q_n>self.MAX_QUEUE and self.q:
                old=self.q.popleft(); self.q_n-=len(old)
            self.cv.notify_all()
        if activated: log('APP relay stream uid=%s call=%s sid=%s'%(uid,callsign,sid))
    def _take(self,want):
        out=array.array('h')
        with self.cv:
            while len(out)<want and self.q:
                a=self.q.popleft(); self.q_n-=len(a); need=want-len(out)
                if len(a)<=need: out.extend(a)
                else:
                    out.extend(a[:need]); self.q.appendleft(a[need:]); self.q_n+=len(a)-need
        return out
    def _publish(self,frame):
        cs=(self.callsign or 'FMO').upper().encode('ascii','ignore')[:12].ljust(12,b'\x00')
        # A8P2 增加 streamBeginUTC，APP 可在快速 PTT 时准确清掉上一段尾音。
        p=bytearray(b'A8P2'); p.append(1)
        p+=(self.seq&0xffff).to_bytes(2,'big'); self.seq=(self.seq+1)&0xffff
        p+=(self.uid&0xffffffff).to_bytes(4,'big')
        p+=(self.sid&0xffffffff).to_bytes(4,'big')
        p+=cs; p+=frame.tobytes()
        try:self.publish_fn(self.TOPIC,bytes(p))
        except Exception as e:log('APP relay publish failed %r'%e)
    def run_player(self):
        nt=time.monotonic(); primed=False
        while not self.stop_flag:
            if not primed:
                with self.cv:
                    while self.q_n==0 and self.pending_n==0 and not self.stop_flag:self.cv.wait(0.5)
                    if self.pending_n>=self.PREBUFFER: self._adopt_pending_locked()
                    # 短流（尤其是 PTT 结束后的 1 帧/80ms 尾包）绝不播放：
                    # 给正常包间隔留 0.8s，凑不满 400ms 的流直接丢弃。
                    while self.q_n<self.PREBUFFER and not self.stop_flag and self.last_rx>0 and time.monotonic()-self.last_rx<0.8:self.cv.wait(0.1)
                    if self.stop_flag:return
                    if self.q_n<self.PREBUFFER:
                        self.q.clear(); self.q_n=0; self.stream=None
                        self._drop_pending_locked()
                        continue
                primed=True; nt=time.monotonic()
                log('APP relay prebuffer ready %d ms'%(self.q_n*1000//8000))
            now=time.monotonic(); active=self.last_rx>0 and now-self.last_rx<2.5
            if not active and self.q_n<self.OUT_N:
                with self.cv:
                    if self.pending_n>=self.PREBUFFER: self._adopt_pending_locked()
                    else: self.q.clear();self.q_n=0;self.stream=None
                primed=False;nt=time.monotonic()+0.04;time.sleep(0.04);continue
            buffered=self.q_n
            now_rate=time.monotonic(); rate_elapsed=now_rate-self.rate_start
            if (not self.fixed_rate) and rate_elapsed>=0.5 and self.rate_samples>0:
                measured=self.rate_samples/rate_elapsed*0.040
                self.rate_samples=0; self.rate_start=now_rate
                target=max(self.RATE_MIN,min(self.RATE_MAX,measured))
                err=buffered-self.PREBUFFER
                target=max(self.RATE_MIN,min(self.RATE_MAX,target+max(-4.0,min(4.0,err*0.0015))))
                self.rate += (target-self.rate)*0.15
            want=max(self.RATE_MIN,min(self.RATE_MAX,int(round(self.rate))))
            src=self._take(want);out=array.array('h',[0]*self.OUT_N)
            if len(src)==0:
                # 语音结束：绝不补 PLC。旧逻辑连续重复上一帧 3 次，听感就是“哒哒哒”。
                self.underruns+=1
                with self.cv:
                    self.q.clear(); self.q_n=0; self.stream=None
                    self._drop_pending_locked()
                primed=False; nt=time.monotonic()+0.04; time.sleep(0.04)
                continue
            else:
                self.plc_gain=1.0
                if len(src)==self.OUT_N:out=src
                else:
                    last=len(src)-1
                    for i in range(self.OUT_N):
                        pos=i*last/(self.OUT_N-1.0);j=int(pos);k=min(last,j+1);f=pos-j
                        out[i]=max(-32768,min(32767,int(round(src[j]*(1.0-f)+src[k]*f))))
                self.last_frame=array.array('h',out)
            self._publish(out);nt+=0.040;d=nt-time.monotonic()
            if d>0:time.sleep(d)
            elif d<-0.4:nt=time.monotonic()

class TxPacer(threading.Thread):
    """下行音频实时节拍器。

    网络语音包是 ~240ms 的突发；若整包立刻灌给 MMDVM-Host，Host→modem
    的缓冲会在两次突发之间欠载，表现为发射断续。这里按 20ms 块、固定节拍
    平滑输出，并负责 FMS/FME 的起停。
    """

    BLOCK_MS = 20
    BLOCK_BYTES = 320          # 160 样本 @8kHz S16LE
    # 严格按实时节拍（20ms 一块）。MMDVM-Host 的 FM 网络缓冲只有
    # 2000 字节 = 1000 样本 = 125ms（FMNetwork.cpp:42），快了会溢出丢音、
    # 慢了会饿死（IQ 只容忍 60ms 断流）——只能靠开头的预灌建立余量。
    PACE_MS = 20
    # 流开始时立即灌入的块数。MMDVM-Host 的 FM 网络环形缓冲只有 2000B=125ms，
    # 必须先把缓冲垫高，否则馈送节拍的微小抖动会让缓冲见底（表现为音频断续）。
    PREBUFFER_BLOCKS = 6       # Host 内层预灌：最多 120ms
    JITTER_BLOCKS = 15         # 桥接层去抖动缓存：300ms，先攒够再稳定输出
    # 网络语音包间隔实测约 250~260ms（BY2AA 交替发 2/3 帧），
    # 静默判定必须显著大于包间隔，否则每个包之间都会 FME/FMS 反复起停 → 发射断续
    IDLE_MS = 800

    def __init__(self, send_fn, start_fn, end_fn):
        super().__init__(daemon=True)
        self._send = send_fn
        self._start = start_fn
        self._end = end_fn
        self._q = queue.Queue()
        self._stop = False
        self._active = False
        self._last = 0.0
        self._last_data = 0.0
        self._next = 0.0
        self._prefill = 0
        self.blocks = 0
        self.underruns = 0
        self.max_empty = 0.0       # 播放中缓冲最长空置时间（判饥饿）
        self._empty_since = 0.0
        self._discard_seq = 0
        self._end_seq = 0
        self._tail_request = False

    def discard(self):
        """Drop queued downlink audio without changing the current TX state."""
        self._discard_seq += 1

    def end_now(self):
        """Drop queued audio and send FME immediately, avoiding a silent tail."""
        self._discard_seq += 1
        self._end_seq += 1

    def feed(self, pcm):
        if pcm:
            self._q.put(pcm)

    def feed_tail(self, pcm):
        """Start a fresh tone stream without the normal 120ms prebuffer."""
        if pcm:
            self._tail_request = True
            self._q.put(pcm)

    def active(self):
        return self._active

    def stop(self):
        self._stop = True

    def run(self):
        buf = bytearray()
        discard_seq = self._discard_seq
        end_seq = self._end_seq
        while not self._stop:
            if discard_seq != self._discard_seq:
                try:
                    while True:
                        self._q.get_nowait()
                except queue.Empty:
                    pass
                buf.clear()
                self._prefill = 0
                self._last_data = 0.0
                self._empty_since = 0.0
                discard_seq = self._discard_seq
            if end_seq != self._end_seq:
                try:
                    while True:
                        self._q.get_nowait()
                except queue.Empty:
                    pass
                buf.clear()
                if self._active:
                    self._end()
                    self._active = False
                self._prefill = 0
                self._last_data = 0.0
                self._empty_since = 0.0
                end_seq = self._end_seq
                continue
            try:
                while True:
                    buf += self._q.get_nowait()
            except queue.Empty:
                pass

            now = time.monotonic()
            has_data = len(buf) >= self.BLOCK_BYTES
            in_hold = (self._active and not has_data
                       and (now - self._last_data) <= self.IDLE_MS / 1000.0)
            jitter_ready = (self._active or self._tail_request or
                            len(buf) >= self.JITTER_BLOCKS * self.BLOCK_BYTES)
            if ((has_data or in_hold) and jitter_ready
                    and (not self._active or now >= self._next or self._prefill > 0)):
                if not self._active:
                    self._active = True
                    self._start()
                    self._next = now
                    self._prefill = 1 if self._tail_request else self.PREBUFFER_BLOCKS
                    self._tail_request = False
                    self._empty_since = 0.0
                    self._last_data = now
                if self._prefill > 0:
                    self._prefill -= 1
                else:
                    now = time.monotonic()
                if has_data:
                    block = bytes(buf[:self.BLOCK_BYTES])
                    del buf[:self.BLOCK_BYTES]
                    self._last_data = now
                else:
                    block = bytes(self.BLOCK_BYTES)
                self._send(block)
                self.blocks += 1
                self._last = now
                self._next = max(self._next, now) + self.PACE_MS / 1000.0

            now = time.monotonic()
            if self._active and not buf:
                if self._empty_since == 0.0:
                    self._empty_since = now
            elif buf:
                if self._empty_since:
                    gap = now - self._empty_since
                    if gap > self.max_empty:
                        self.max_empty = gap
                    if gap > 0.060:
                        self.underruns += 1
                    self._empty_since = 0.0

            if self._active and (now - self._last_data) > self.IDLE_MS / 1000.0:
                self._end()
                self._active = False
                buf.clear()
                self._empty_since = 0.0

            if self._active:
                dt = self._next - time.monotonic()
                time.sleep(0.0005 if dt <= 0 else min(dt, 0.02))
            else:
                time.sleep(0.003)

class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        c = cfg["client"]
        self.uid = int(c["uid"])
        self.callsign = str(c["callsign"]).upper()
        self.self_aliases = {str(x).strip().upper() for x in (c.get("self_aliases") or []) if str(x).strip()}
        self.vendor = int(cfg.get("fm", {}).get("vendor", PK.DEFAULT_VENDOR))
        self.codec_mode = cfg.get("voice", {}).get("codec", "opus").lower()
        # 语音主题：默认直连 FMO/RAW；设为 FMO-BRIDGE/RAW 时由服务器侧中继
        # 改写身份后转出（真实设备只信任官方根，故服务器侧改写是必要的一步）
        self.topic = cfg.get("voice", {}).get("topic") or RAW_TOPIC
        # 是否同时以「服务器本台身份」把上行音频转发到全网 FMO/RAW
        self.relay_out = bool(cfg.get("voice", {}).get("relay_to_public", True))
        self.relay_pkts = 0
        self.relayed_sb = {}          # 本机转出过的 streamBeginUTC → 时间（下行去重用）
        self._tx_frame_hashes = {}    # 本机上行的帧内容哈希 → 时间（防自回声）
        # 下行收包统计（诊断丢包）
        self.rx_msgs = 0
        self.rx_drop_own = 0
        self.rx_drop_self = 0
        self.rx_drop_echo = 0
        self.rx_drop_bad = 0
        self.rx_drop_busy = 0
        self.rx_drop_dup = 0
        self.rx_drop_ctcss = 0
        self._ctcss_gate_prev = False
        self._dedup = {}
        self.rx_frames = 0
        self.cap_n = 0
        self._stat_t = 0.0

        # 无本地证书时自动向服务器注册（方案 C）
        reg = c.get("register_url") or cfg.get("server", {}).get("register_url")
        if reg:
            try:
                import fmo_reg
                srv_info, _, _ = fmo_reg.ensure_cert(
                    reg, self.callsign, self.uid,
                    c["user_cert"], c["user_key"], c["intermediate_cert"],
                    token=c.get("register_token"), log=log)
                if srv_info:
                    s = cfg["server"]
                    s["callsign"] = srv_info.get("callsign", s.get("callsign"))
                    s["uid"] = srv_info.get("uid", s.get("uid"))
                    s["certFingerprint"] = srv_info.get("certFingerprint",
                                                        s.get("certFingerprint"))
                    if not s.get("mqtt_host"):
                        s["mqtt_host"] = srv_info.get("mqttHost")
                    if not s.get("mqtt_port"):
                        s["mqtt_port"] = srv_info.get("mqttPort", 1883)
                    log("已从服务器取得身份：%s uid=%s" % (s["callsign"], s["uid"]))
            except Exception as e:
                log("自动注册未完成（改用本地已有证书）：%s" % e)

        # No certificate?  Still start the local RF side so FM tail tone and
        # local repeat keep working; only network interconnection is deferred.
        self.local_only = False
        try:
            with open(c["user_cert"], encoding="utf-8") as f:
                self.user_cert = json.load(f)
            with open(c["intermediate_cert"], encoding="utf-8") as f:
                self.inter_cert = json.load(f)
            with open(c["user_key"], encoding="ascii") as f:
                self.user_seed = P.b64u_decode(f.read().strip())
            self.uid = int(self.user_cert["subject"]["uid"])
        except Exception as e:
            self.local_only = True
            self.user_cert = {}
            self.inter_cert = {}
            self.user_seed = b""
            self.uid = int(c.get("uid") or 0)
            log("FMO 证书未注册，进入本地 FM/尾音模式（网络互联未启动）：%s" % e)

        self.codec = OpusCodec() if self.codec_mode == "opus" else RadPcmCodec()
        self.frame_samples = OPUS_FRAME if self.codec_mode == "opus" else RADPCM_FRAME
        self.frame_ms = self.frame_samples * 1000 // 8000     # OPUS 40ms / RADPCM 80ms
        self.cm = PK.CM_OPUS if self.codec_mode == "opus" else PK.CM_RADPCM
        # 下行必须按对端实际编码解码，故两种解码器都常驻
        self.opus = OpusCodec()
        self.radpcm = RadPcmCodec()

        # 在线宣告（真实设备靠 FMO/TELE 感知在线用户）
        # 注意：午夜实测可用时**没有**发 TELE；若开启后干扰对端播放，可通过配置关闭
        self.presence_on = bool(cfg.get("voice", {}).get("presence", False))
        # §8.4 路由仲裁：午夜可用时没有实现；实现不当会误拒真实设备的包（表现为断续）
        self.arbitration_on = bool(cfg.get("voice", {}).get("arbitration", False))
        self.session = int(time.time() * 1000) & 0xFFFFFFFF
        self.late_seq = 0
        self.stop_flag = False
        # §8.4 网络路由仲裁状态
        self.route_uid = None
        self.route_sb = 0
        self.route_at = 0.0
        # 上行（RF -> 网络）
        self.rx_buf = bytearray()
        # 上行速率计量（诊断 FM -> 网络 是否按实时速率送音频）
        self.rf_pkts = 0
        self.rf_samples = 0
        self.rf_rate_t0 = time.time()
        self.rf_gap_max = 0.0
        self.rf_last_t = 0.0
        self.frames = []           # [(cm, payload), ...]
        self.stream_begin = 0
        self.seq = 0
        self.tx_stream_on = False

        self.last_voice = 0.0
        self.last_net_voice = 0.0
        # 下行（网络 -> RF）
        self.rf = None
        self.pacer = None          # 下行实时节拍器（run() 里创建）
        # 下行音频整形（见 fmo_dsp.py；参数在 config.json 的 fm.dsp）
        self.shaper = DownlinkShaper(8000, cfg.get("fm", {}).get("dsp") or {})
        self.nw_playing = False

        self.nw_frames = 0
        self.rf_busy_until = 0.0   # 半双工：本地正在发就不收网络
        self.net_guard_until = 0.0 # 网络正在放音/刚放完时抑制 SDR 自回声
        self.rf_end_time = 0.0      # 最近一次射频流结束时刻（防止尾包回放）
        self.agg_ms = 0            # 上行聚合计时（毫秒）
        # 断续诊断
        self.rx_last = 0.0         # 上行：上一个 FMD 到达时刻
        self.rx_max_gap = 0.0
        self.tx_pkts = 0           # 上行：本流已发布的消息包/帧
        self.tx_frames = 0
        self.nw_last = 0.0         # 下行：上一个网络包到达时刻
        self.nw_max_gap = 0.0
        # 上行音频抓取（诊断用，fm.capture 指定路径时启用）
        self.cap_path = self.cfg.get("fm", {}).get("capture") or ""
        self.cap_w = None
        self.cap_bytes = 0
        if self.cap_path:
            try:
                self.cap_w = wave.open(self.cap_path, "wb")
                self.cap_w.setnchannels(1)
                self.cap_w.setsampwidth(2)
                self.cap_w.setframerate(8000)
                log("上行音频抓取 ->", self.cap_path)
            except Exception as e:
                log("上行音频抓取开启失败：%s" % e)
                self.cap_w = None
        # 下行音频抓取（诊断用）：记录实际送给 Host 的 PCM（裸 PCM，避免 wave 模块问题）
        self.cap_tx_path = self.cfg.get("fm", {}).get("capture_tx") or ""
        self.cap_tx = None
        # ---- 通联日志（供 Web「Live QSO」看板显示呼号）----
        self.qso_ch = self._detect_qso_channel(cfg)   # 本桥接对应的 MMDVM 通道号
        self.nw_start = 0.0       # 网络流开始时刻
        self.nw_call = ""        # 网络流说话人呼号
        self.nw_uid = 0
        self.tx_start = 0.0       # RF 流开始时刻

        if self.cap_tx_path:
            try:
                self.cap_tx = open(self.cap_tx_path, "wb")
                log("下行音频抓取（裸 PCM @8kHz s16le）->", self.cap_tx_path)
            except Exception as e:
                log("下行音频抓取开启失败：%s" % e)
                self.cap_tx = None

        s = cfg["server"]
        self.mqtt = mqtt.Client(client_id="fmo-bridge-" + self.callsign,
                                clean_session=True, protocol=mqtt.MQTTv311)
        if not self.local_only:
            self._refresh_login()
            self.mqtt.on_connect = self._on_connect
            self.mqtt.on_message = self._on_message_safe
            self.mqtt.on_disconnect = self._on_disconnect
        else:
            log("FMO 本地模式：不连接 MQTT，FM 尾音和本地转发可用")
        # Only the central 93 bridge may publish the APP A8P2 stream.
        # A local bridge still handles FM<->FMO but must not emit a second A8P2.
        self.app_relay_enabled = bool(cfg.get("app_relay_enabled", True))
        self.app_relay = AppPcmRelay(lambda topic,payload: self.mqtt.publish(topic,payload,qos=0))

    # --------------------------------------------- §8.4 网络路由仲裁（协议强制）
    ROUTE_WINDOW_MS = 1500

    def _route_accept(self, uid, stream_begin):
        """按 FMO 协议 §8.4 判定该语音包是否有权占用网络路由。

        规则：同 UID 续占；路由超时(1500ms)新包占用；
             新包 streamBeginUTC 更早(差值≤2s) 抢占；相同且 UID 更小 抢占；否则拒绝。
        """
        now = time.time() * 1000.0
        if (self.route_uid is not None
                and (now - self.route_at) <= self.ROUTE_WINDOW_MS):
            if uid == self.route_uid:
                self.route_at = now
                return True
            diff = (self.route_sb - stream_begin) & 0xFFFFFFFF
            if stream_begin != self.route_sb and 0 < diff <= 2000:
                log("路由被抢占：uid=%d 取代 uid=%d（起始更早 %dms）"
                    % (uid, self.route_uid, diff))
                self.route_uid, self.route_sb, self.route_at = uid, stream_begin, now
                return True
            if stream_begin == self.route_sb and uid < self.route_uid:
                log("路由被抢占：uid=%d 取代 uid=%d（起始相同，UID 更小）"
                    % (uid, self.route_uid))
                self.route_uid, self.route_sb, self.route_at = uid, stream_begin, now
                return True
            return False
        self.route_uid, self.route_sb, self.route_at = uid, stream_begin, now
        return True

    def _route_free(self):
        """本机上行结束：让出路由，供他人使用。"""
        self.route_uid = None
        self.route_sb = 0
        self.route_at = 0.0

    # ------------------------------------------------------------- MQTT
    def _refresh_login(self):
        """(重新)生成 MQTT 登录凭据。

        登录密码里含有时效性 timestamp，SAS 有 TtlSec 窗口；
        paho 自动重连会复用旧凭据 → 超窗后被 deny(rc=5)，故每次重连前必须刷新。
        """
        s = self.cfg["server"]
        c = self.cfg["client"]
        login = P.build_mqtt_login(
            self.user_cert, self.inter_cert, self.user_seed,
            server_callsign=s["callsign"], server_uid=int(s["uid"]),
            mqtt_host=s["mqtt_host"], mqtt_port=int(s["mqtt_port"]),
            server_fingerprint_b64u=s["certFingerprint"],
            role=c.get("role", "user"))
        self.mqtt.username_pw_set(login["username"], login["password"])
        return login

    def _on_connect(self, cli, userdata, flags, rc):
        if rc == 0:
            # 订阅与真实 FMO 设备一致的主题集合
            for t in PRES.USER_SUBSCRIPTIONS:
                cli.subscribe(t, qos=0)
            if self.topic != "ARCH/HRC":
                cli.subscribe("ARCH/HRC", qos=0)   # 下行：直接收全网语音
                cli.subscribe(self.topic, qos=0)
            log("MQTT 已连接；下行主题=FMO/RAW，中转主题=%s" % self.topic)
            self._send_presence()          # 立即宣告一次"我在线"
        else:
            log("MQTT 连接失败 rc=%s（SAS 可能 deny：检查证书/指纹/时间）" % rc)

    # ----------------------------------------------------- 在线宣告（关键）
    def _send_presence(self):
        """发 FMO/TELE 遥测/在线宣告（默认关闭，见 voice.presence）。"""
        if not self.presence_on:
            return
        fmc = self.cfg.get("fm", {})
        tele = PRES.build_tele(self.uid, self.callsign,
                               tx_mhz=float(fmc.get("tx_mhz", 0.0)),
                               rx_mhz=float(fmc.get("rx_mhz", 0.0)),
                               session=self.session)
        try:
            self.mqtt.publish("FMO/TELE", tele, qos=0)
            self.mqtt.publish("FMO/LATE/UID_V1/%d" % self.uid,
                              PRES.build_late(0, self.late_seq, 0), qos=0)
            self.late_seq = (self.late_seq + 1) & 0xFFFFFFFF
        except Exception as e:
            log("宣告发送失败：%s" % e)

    def _presence_loop(self):
        while not self.stop_flag:
            time.sleep(30.0)
            if self.mqtt is not None:
                self._send_presence()

    @staticmethod
    def _detect_qso_channel(cfg):
        """Read the live channel plan every time a QSO record is written.
        Any channel can be switched to FM at any time, so the FM QSO channel
        must not be frozen at bridge startup."""
        try:
            plan = json.load(open("/etc/mmdvm/channels.json", encoding="utf-8"))
            for item in plan.get("list") or []:
                if str(item.get("mode") or "").upper() == "FM":
                    return int(item.get("n") or 1)
        except Exception:
            pass
        try:
            explicit = (cfg.get("fm") or {}).get("channel")
            if explicit is not None:
                return int(explicit)
        except Exception:
            pass
        return 1

    def _cred_loop(self):
        """高频刷新登录凭据。

        SAS 的凭据有效期只有约 60 秒（其启动日志明确写着
        「Token 过期时间随机抖动(±60s, 避免惊群)」）。若刷新间隔与有效期相当，
        重连时凭据往往刚好过期 → 永久 rc=5。故这里每 15 秒重建一次。
        """
        while not self.stop_flag:
            time.sleep(15.0)
            try:
                self._refresh_login()
            except Exception as e:
                log("刷新登录凭据失败：%s" % e)

    def _on_disconnect(self, cli, userdata, rc):
        log("MQTT 断开 rc=%s；刷新登录凭据供重连" % rc)
        try:
            self._refresh_login()
        except Exception as e:
            log("刷新登录凭据失败：%s" % e)

    def _on_message_safe(self, cli, userdata, msg):
        try:
            self._on_message(cli, userdata, msg)
        except Exception as e:
            log("下行消息处理失败：%r" % e)

    def _on_message(self, cli, userdata, msg):
        self.rx_msgs += 1
        if msg.topic not in ("ARCH/HRC", self.topic):
            return
        # 丢弃自己转出的包（uid=服务器 且 streamBeginUTC 命中），否则会自激回环
        if self._is_own_relay(msg.payload):
            self.rx_drop_own += 1
            return
        try:
            pkt = PK.parse_voice_packet(msg.payload)
        except Exception as e:
            self.rx_drop_bad += 1
            if self.rx_drop_bad <= 5:
                log("解包失败:", e)
            return
        pkt_callsign = str(pkt.get("callsign") or "").strip().upper()
        same_identity = (pkt["uid"] == self.uid or
                         (pkt_callsign and (pkt_callsign == self.callsign or
                                            pkt_callsign in self.self_aliases)))
        now = time.time()
        # The same UID/callsign is also used by the operator's phone APP.
        # Suppress own-identity downlink only while the local RF is active or
        # within the short return window after PTT.  An idle same-identity
        # packet is legitimate APP -> FMO audio and must reach the RF side.
        # Exact transmitted-frame hashes below still catch relayed RF echoes.
        if same_identity and (self.tx_stream_on or (now - self.last_voice) < 2.0):
            self.rx_drop_self += 1
            return
        # If the server rewrites or reflects our own RF uplink metadata, the
        # callsign/UID checks above can miss the returned copy. Drop it by
        # matching the encoded frame contents we actually transmitted.
        for _cm, _payload in pkt.get("frames", []):
            _seen = self._tx_frame_hashes.get(hashlib.sha1(_payload).digest())
            if _seen is not None and (now - _seen) < 8.0:
                self.rx_drop_echo += 1
                return
        # ARCH 2026-09-19：同一条语音流可能同时出现在 ARCH/HRC 与 FMO/RAW
        # （服务器侧镜像或双发），按报文内容去重，避免重复灌入节拍器 → 叠音
        key = hashlib.sha1(msg.payload).digest()
        seen = self._dedup.get(key)
        if seen is not None and (now - seen) < 2.0:
            self.rx_drop_dup += 1
            return
        self._dedup[key] = now
        if len(self._dedup) > 4000:
            cut = now - 5.0
            self._dedup = {k: v for k, v in self._dedup.items() if v >= cut}
        self._on_net_voice(pkt)

    # ------------------------------------------------------------- 上行 RF->网络
    def _ctcss_gate_open(self):
        # [FIX 1.0.128] Do NOT discard the whole uplink when the external CTCSS
        # state file is missing or stale. MMDVM-Host already gates on CTCSS
        # (AccessMode=1 + CTCSSFrequency) and only forwards FM Network audio
        # while CTCSS is present. /run/arch-fm-ctcss.state has no writer, so
        # requiring it silently dropped every RF->network transmission.
        # Honour the file only when it exists, is fresh, and equals "1"/"0".
        path = "/run/arch-fm-ctcss.state"
        try:
            if (time.time() - os.path.getmtime(path)) > 2.0:
                return True
            with open(path, "r", encoding="ascii") as f:
                return f.read().strip() == "1"
        except Exception:
            return True

    def on_rf_audio(self, pcm):
        now = time.time()
        # The MMDVM-Host FM network can still deliver demodulated audio even
        # when the modem's CTCSS gate is closed. Drop that audio at the FMO
        # uplink entrance unless the modem reports an open CTCSS gate.
        ctcss_open = self._ctcss_gate_open()
        if not ctcss_open:
            # End the network stream immediately when CTCSS drops, instead of
            # waiting for STREAM_IDLE_S and letting the remote buffer play
            # residual noise after PTT release.
            if self._ctcss_gate_prev and self.tx_stream_on:
                self.flush_stream(discard_tail=True)
            self._ctcss_gate_prev = False
            self.rx_drop_ctcss += 1
            if self.rx_drop_ctcss <= 5 or (self.rx_drop_ctcss % 100) == 0:
                log("RF->网络丢弃：CTCSS 未开门 (%d)" % self.rx_drop_ctcss)
            return
        self._ctcss_gate_prev = True
        # Half-duplex/self-echo guard: while a network stream is actively being
        # played to RF, the SDR may hear our own transmission and return it as
        # RF audio. Never feed that echo back into the FMO room.
        if (self.nw_playing and (now - self.last_net_voice) < 0.8) or now < self.net_guard_until:
            self.rx_drop_busy += 1
            return
        if self.cap_w is not None:
            try:
                self.cap_w.writeframes(pcm)
                self.cap_bytes += len(pcm)
                if self.cap_bytes > 8000 * 2 * 180:      # 上限 180 秒
                    self.cap_w.close()
                    log("上行音频抓取已达上限，停止写入 %s" % self.cap_path)
                    self.cap_w = None
            except Exception:
                self.cap_w = None
        self.rf_pkts += 1
        self.rf_samples += len(pcm) // 2
        if self.rf_last_t > 0:
            g = now - self.rf_last_t
            if g > self.rf_gap_max:
                self.rf_gap_max = g
        self.rf_last_t = now
        if self.rx_last:
            gap = now - self.rx_last
            if gap > self.rx_max_gap:
                self.rx_max_gap = gap
        self.rx_last = now
        if now - self.last_voice > STREAM_IDLE_S:
            self._begin_stream()
        self.last_voice = now
        self.rf_busy_until = now + 1.0        # §8.3 半双工：当前方向空闲超过 1s 才允许反向
        self.rx_buf += pcm_gain(pcm, float(self.cfg.get("fm", {}).get("rxgain", 1.0)))

        need = self.frame_samples * 2
        while len(self.rx_buf) >= need:
            chunk = bytes(self.rx_buf[:need])
            del self.rx_buf[:need]
            self.frames.append((self.cm, self.codec.encode(chunk)))
            self.agg_ms += self.frame_ms
            # 再凑一帧就超过 250ms 就发（RADPCM 3帧=240ms，OPUS 6帧=240ms）
            # 必须与对端(BY2AA)一致：240ms 一发，否则接收端抖动缓冲会欠载 → 断续
            if self.agg_ms + self.frame_ms > PK.AGG_MS:
                self._flush_frames()
                self.agg_ms = 0

    def _begin_stream(self):
        self.flush_stream(discard_tail=True)
        self.stream_begin = int(time.time() * 1000) & 0xFFFFFFFF
        self.seq = 0
        self.agg_ms = 0
        self.tx_stream_on = True
        self.tx_start = time.time()
        self._qso_log("rf", self.callsign, "CQCQCQ")   # 本台开始发射

        log("RF 开始 -> 网络流（codec=%s vendor=0x%04X）"
            % (self.codec_mode, self.vendor))

    def _relay_out(self, blob):
        """把上行包以「服务器本台身份」改写后发到全网 FMO/RAW。

        真实 FMO 设备只信任能链到官方根的用户证书，我们自签身份不会被它们播放；
        按官方《语音数据开放协议》「服务器管理员对桥接输入源负责」及使用说明书中
        「部分服务器可能桥接了非 FMO 的音频源或呼号输入」，由服务器台以本台身份
        转出是被认可的用法。改写只动头部，CRC32 仅覆盖帧区，无需重算。

        关键：vendor 也要写成 0x0000（FMO 官方值）。协议规定 vendor 的用途是
        「让大家都遵守同一种语音编码规则」，0x1000~0x2FFF 的试验/软件区只是
        开发调试用；真实设备对软件区厂商的流不予播放（实测：会提示接收但不放音）。
        """
        s = self.cfg["server"]
        b = bytearray(blob)
        struct.pack_into("<I", b, 2, 0)                       # vendor = 0x0000（FMO）
        struct.pack_into("<I", b, 6, int(s["uid"]) & 0xFFFFFFFF)
        b[10:22] = str(s["callsign"]).upper().encode("ascii")[:12].ljust(12, b"\x00")
        struct.pack_into("<I", b, 41, int(s["uid"]) & 0xFFFFFFFF)
        out = bytes(b)
        # 记录本次转出的流标识，供下行去重（防回环）
        sb = struct.unpack_from("<I", out, 22)[0]
        self.relayed_sb[sb] = time.time()
        self.mqtt.publish("ARCH/HRC", out, qos=0)
        self.relay_pkts += 1

    def _is_own_relay(self, payload):
        """判断该下行包是否就是我们自己刚转出的（uid=服务器 且 streamBeginUTC 命中）。"""
        if len(payload) < 26:
            return False
        if struct.unpack_from("<I", payload, 6)[0] != int(self.cfg["server"]["uid"]):
            return False
        sb = struct.unpack_from("<I", payload, 22)[0]
        t = self.relayed_sb.get(sb)
        if t is not None and (time.time() - t) < 30.0:
            return True
        return False

    def _flush_frames(self):
        if not self.frames:
            return
        pkts = PK.chunk_frames(self.frames)
        now = time.time()
        for fr in pkts:
            for _cm, _payload in fr:
                self._tx_frame_hashes[hashlib.sha1(_payload).digest()] = now
        if len(self._tx_frame_hashes) > 8000:
            cut = now - 10.0
            self._tx_frame_hashes = {k: v for k, v in self._tx_frame_hashes.items() if v >= cut}
        self.frames = []
        for fr in pkts:
            blob = PK.build_voice_packet(self.uid, self.callsign, fr,
                                         stream_begin_utc=self.stream_begin,
                                         srv_uid=int(self.cfg["server"]["uid"]),
                                         vendor=self.vendor,
                                         seq_index0=1)   # 官方实现：每个消息包内序号从 1 起
            self.seq += len(fr)
            self.mqtt.publish(self.topic, blob, qos=0)
            if self.relay_out:
                try:
                    self._relay_out(blob)      # 同时以服务器本台身份发到全网
                except Exception as e:
                    log("转发到全网失败（不影响本地路径）：%s" % e)
            self.tx_pkts += 1
            self.tx_frames += len(fr)

    def flush_stream(self, discard_tail=False):
        if self.frames:
            if discard_tail:
                # Drop the final partial aggregation on PTT release. Sending it
                # produces the two short clicks heard by other FMO terminals.
                self.frames.clear()
                self.agg_ms = 0
            else:
                self._flush_frames()
        if self.tx_stream_on:
            log("RF 流收尾：发布 %d 包 / %d 帧，最大到达间隙 %.0fms"
                % (self.tx_pkts, self.tx_frames, self.rx_max_gap * 1000.0))
        self.tx_stream_on = False
        self.tx_pkts = 0
        if self.tx_start > 0:
            self._qso_log("rf", self.callsign, "CQCQCQ",
                          max(0.1, time.time() - self.tx_start))
            self.tx_start = 0.0
        self.tx_frames = 0
        self.rx_max_gap = 0.0
        self.rx_buf.clear()
        self._route_free()                 # §8.4 结束上行即让出路由

    def tick(self):
        if self.tx_stream_on and (time.time() - self.last_voice) > STREAM_IDLE_S:
            self.flush_stream(discard_tail=True)

    # ------------------------------------------------------------- 下行 网络->RF
    def _decode_any(self, cm, payload):
        """按帧自带的 compressMode 解码（对端可能是 OPUS/RADPCM/PCM 任意一种）。"""
        try:
            if cm == PK.CM_OPUS:
                return self.opus.decode(payload)
            if cm == PK.CM_RADPCM:
                return self.radpcm.decode(payload)
            if cm == PK.CM_PCM:
                return payload
        except Exception as e:
            log("解码失败 cm=%d len=%d: %s" % (cm, len(payload), e))
        return b""

    def _on_net_voice(self, pkt):
        if self.rf is None:
            return
        if time.time() < self.rf_busy_until:      # 半双工：本地正在发就不收网络
            self.nw_last = 0.0                    # 避免把本地发射期间算作网络间隙
            self.rx_drop_busy += 1
            return
        # §8.4 网络路由仲裁（默认关闭，见 voice.arbitration）
        if self.arbitration_on and not self._route_accept(pkt["uid"], pkt["streamBeginUTC"]):
            return
        cs = pkt["callsign"]
        if not self.nw_playing:
            self.nw_playing = True
            self.nw_start = time.time()
            self.nw_last = 0.0          # 新流：重置到达间隙统计
            self.nw_max_gap = 0.0
            self.nw_call = cs
            self._qso_log("network", cs, "CQCQCQ")   # 对方开始说话

            log("网络 -> RF: %s uid=%d cm=%s frames=%d"
                % (cs, pkt["uid"], sorted(set(cm for cm, _ in pkt["frames"])),
                   len(pkt["frames"])))
        self.last_net_voice = time.time()
        self.net_guard_until = self.last_net_voice + 1.5
        now = self.last_net_voice
        if self.nw_last:
            gap = now - self.nw_last
            if gap > self.nw_max_gap:
                self.nw_max_gap = gap
        self.nw_last = now
        gain = float(self.cfg.get("fm", {}).get("txgain", 1.0))
        for cm, payload in pkt["frames"]:
            pcm = self._decode_any(cm, payload)
            if pcm:
                if self.app_relay_enabled:
                    self.app_relay.feed(pcm, pkt['uid'], cs, pkt['streamBeginUTC'])
                # 必须交给节拍器平滑输出：直接整包灌给 Host 会撑爆其 125ms 环形缓冲
                # （FMNetwork.cpp:42 m_buffer(2000U)），导致音频被丢弃 → 断续
                pcm = self.shaper.process(pcm_gain(pcm, gain))   # 下行音频整形
                if self.pacer is not None:
                    self.pacer.feed(pcm)
                else:
                    self.rf.send_audio(pcm)
                self.nw_frames += 1
                self.rx_frames += 1

    def _on_rf_end(self):
        now_end = time.time()
        if now_end - getattr(self, "_last_rf_end", 0.0) < 0.35:
            log("忽略重复 RF END")
            return
        self._last_rf_end = now_end
        self.rf_end_time = now_end
        had_uplink = self.tx_frames >= 1
        tone_on = bool((self.cfg.get("fm", {}) or {}).get("tone", {}).get("enable"))
        self.flush_stream(discard_tail=True)
        if self.pacer is not None:
            # End the old Host stream immediately so its 125ms FM ring buffer
            # cannot leak stale noise before the local confirmation tone.
            self.pacer.end_now()
        log("RF END")
        if had_uplink:
            self._courtesy_tone()

    def _courtesy_tone(self):
        """尾音确认音：对方松开 PTT（Host 发 FME）后回一段短促音。
        走同一个 TxPacer，因此 FMS/FME 与节拍都由它统一负责。"""
        t = (self.cfg.get("fm", {}) or {}).get("tone") or {}
        if not t.get("enable") or self.pacer is None:
            return
        def _play():
            # Wait for the old FME stream to finish, then start one clean
            # tone stream without the normal 120ms prebuffer.
            deadline = time.monotonic() + 0.25
            while self.pacer.active() and time.monotonic() < deadline:
                time.sleep(0.005)
            try:
                self.pacer.feed_tail(make_tone_pcm(float(t.get("freq", 1000.0)),
                                                  int(t.get("ms", 200)),
                                                  1.0, 8000, 20))
                log("尾音确认音 %dHz %dms（对方已收到）"
                    % (int(t.get("freq", 1000)), int(t.get("ms", 200))))
            except Exception as e:
                log("尾音发送失败：%s" % e)
        threading.Thread(target=_play, daemon=True).start()

    def _qso_log(self, src, call, target, seconds=None, extra=""):
        """按 MMDVM-Host 的格式打一条通联日志，供 Web「Live QSO」解析。

        格式必须同时满足两处：
          · mmdvm-status.py 的关键字过滤：含 'received network voice header'
            / 'received rf voice header'（连在一起的固定词组）
          · app.py 的 parse_qso_line：含 'fm' → 模式 FM；'from <呼号>'；
            'N seconds'；'\\s*\\[CH(\\d)\\]\\s*' 通道前缀
        故输出形如：
          开始通话：[CH1] 2026-09-12 09:30:15 FM received network voice header "
                    "from BY2AA to CQCQCQ            ← 不带时长 = “通话中”
          结束通话：[CH1] 2026-09-12 09:30:20 FM received network voice header "
                    "from BY2AA to CQCQCQ, 5.2 seconds
        ★ FM_QSO_LIVE_FIX：开始那一行绝对不能再带 “N seconds”，
          否则 parse_qso_line 会把它当成已结束，通联列表不会变红。
        """
        try:
            self.qso_ch = self._detect_qso_channel(self.cfg)
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            who = "network" if src == "network" else "rf"
            if seconds is None:
                log("[CH%d] %s FM received %s voice header from %s to %s%s"
                    % (self.qso_ch, ts, who, call or "UNKNOWN",
                       target or "CQCQCQ", extra))
            else:
                log("[CH%d] %s FM received %s voice header from %s to %s, %.1f seconds%s"
                    % (self.qso_ch, ts, who, call or "UNKNOWN",
                       target or "CQCQCQ", max(0.1, seconds), extra))
        except Exception as e:
            log("通联日志写入失败：%s" % e)

    def _check_tone_req(self):
        """Web 页「试听尾音」：读请求文件内容，与上次不同就发一声。

        不用删除文件：桥接以 pi 身份运行，而请求文件由 root 写进 /tmp
        （/tmp 带 sticky 位），pi 无权 unlink，只能靠内容比对。
        """
        if self.pacer is None:
            return
        try:
            with open("/tmp/fmo_tone_req") as f:
                v = f.read().strip()
        except Exception:
            return
        if not v:
            return
        seen = getattr(self, "_tone_seen", None)
        if seen is None:
            # Do not fire a stale request left over from before a restart.
            self._tone_seen = v
            return
        if v == seen:
            return
        self._tone_seen = v
        t = dict((self.cfg.get("fm", {}) or {}).get("tone") or {})
        try:
            self.pacer.feed(make_tone_pcm(float(t.get("freq", 1000.0)),
                                         int(t.get("ms", 200)),
                                         float(t.get("gain", 1.0))))
            log("试听尾音已发送（%dHz %dms）"
                % (int(t.get("freq", 1000)), int(t.get("ms", 200))))
        except Exception as e:
            log("试听尾音失败：%s" % e)

    def _net_idle(self):
        # FMS/FME 由 TxPacer 负责；这里只做收尾统计
        if (self.nw_playing and (self.pacer is None or not self.pacer.active())
                and (time.time() - self.last_net_voice) > TxPacer.IDLE_MS / 1000.0):
            self.nw_playing = False
            if self.nw_start > 0:
                self._qso_log("network", self.nw_call, "CQCQCQ",
                              max(0.1, time.time() - self.nw_start))
                self.nw_start = 0.0
            log("网络流结束：解码 %d 帧，最大到达间隙 %.0fms，流中断音 %.0fms（%d 次）"
                % (self.nw_frames, self.nw_max_gap * 1000.0,
                   (self.pacer.max_empty * 1000.0) if self.pacer else 0.0, self.pacer.underruns if self.pacer else 0))
            if self.pacer:
                self.pacer.max_empty = 0.0
                self.pacer.underruns = 0
            self.nw_frames = 0
            self.nw_max_gap = 0.0

    # ------------------------------------------------------------- 运行
    def _rf_rate_report(self):
        now = time.time()
        el = now - self.rf_rate_t0
        if el < 5.0:
            return
        if self.rf_pkts > 0:
            audio_ms = self.rf_samples * 1000.0 / 8000.0
            pct = audio_ms / (el * 1000.0) * 100.0
            log("[上行速率] %d 包 / %.2fs 音频 / %.1fs 墙钟 = %.0f%%  最大块间隔 %.0fms"
                % (self.rf_pkts, audio_ms / 1000.0, el, pct, self.rf_gap_max * 1000.0))
        self.rf_pkts = 0
        self.rf_samples = 0
        self.rf_gap_max = 0.0
        self.rf_rate_t0 = now

    def run(self):
        fmc = self.cfg["fm"]
        self.rf = FmNetwork(fmc.get("bind_addr", "127.0.0.1"),
                            int(fmc["bind_port"]),
                            callsign=self.callsign,
                            on_audio=self.on_rf_audio,
                            on_start=lambda cs: log("RF START from", cs),
                            on_end=self._on_rf_end,
                            debug=bool(fmc.get("debug", False)))
        self.rf.open().start_thread()

        def _send_block(pcm):
            if self.cap_tx is not None:
                try:
                    self.cap_tx.write(pcm)
                    self.cap_tx.flush()
                    self.cap_n += 1
                except Exception as e:
                    log("下行抓取写入失败：%s" % e)
                    self.cap_tx = None
            elif self.cap_n == 0:
                self.cap_n = -1
                log("注意：下行抓取未启用（cap_tx 为空）")
            self.rf.send_audio(pcm)

        self.pacer = TxPacer(_send_block, self.rf.send_start, self.rf.send_end)
        self.pacer.start()
        log("FM Network 监听 %s:%s（下行按 20ms 块实时节拍输出）"
            % (fmc.get("bind_addr", "127.0.0.1"), fmc["bind_port"]))

        s = self.cfg["server"]
        if not self.local_only and s.get("mqtt_host") and s.get("callsign") and int(s.get("uid") or 0) > 0:
            log("连接 MQTT %s:%s (server=%s uid=%s)" % (s["mqtt_host"], s["mqtt_port"],
                                                       s["callsign"], s["uid"]))
            self.mqtt.connect(s["mqtt_host"], int(s["mqtt_port"]), keepalive=30)
            self.mqtt.loop_start()
            if self.app_relay_enabled:
                self.app_relay.start()
            threading.Thread(target=self._presence_loop, daemon=True).start()
            threading.Thread(target=self._cred_loop, daemon=True).start()
            log("在线宣告已启动（FMO/TELE 每 30s，频率 TX=%s/RX=%s MHz）"
                % (fmc.get("tx_mhz", 0), fmc.get("rx_mhz", 0)))
        else:
            log("FMO 本地模式：网络互联未启动，FM 尾音和本地射频转发继续工作")

        while True:
            time.sleep(0.05)
            self.tick()
            self._net_idle()
            self._rf_rate_report()
            self._check_tone_req()
            now = time.time()
            if now - self._stat_t >= 30.0:
                self._stat_t = now
                log("[下行统计] 收到 %d 包 → 解码 %d 帧；丢弃 本机中转 %d / 本机自己 %d "
                    "/ 自回声 %d / 解包失败 %d / 半双工 %d / 重复 %d"
                    % (self.rx_msgs, self.rx_frames, self.rx_drop_own,
                       self.rx_drop_self, self.rx_drop_echo, self.rx_drop_bad,
                       self.rx_drop_busy, self.rx_drop_dup))
                # AGC / 限幅统计（判断音频电平是否被拉平）
                try:
                    _st = self.shaper.stats()
                    log("[音频AGC] 输入峰值 %.2f → 输出峰值 %.2f；输出 RMS %.1f%%；"
                        "AGC 增益 %.2f（最低 %.2f）；限幅触发 %d 次"
                        % (_st["pk_in"], _st["pk_out"], _st["rms_out"] * 100.0,
                           _st["gain_now"], _st["gain_min"], _st["clip"]))
                    self.shaper.reset_stats()
                except Exception as e:
                    log("AGC 统计失败：%s" % e)

def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    b = Bridge(cfg)

    def _stop(*_):
        log("退出中…")
        try:
            b.flush_stream()
            b.stop_flag = True
            if b.pacer:
                b.pacer.stop()
            if b.cap_w:
                b.cap_w.close()
            if b.cap_tx:
                b.cap_tx.close()
            b.mqtt.loop_stop()
            b.rf.close()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    b.run()

if __name__ == "__main__":
    main()



