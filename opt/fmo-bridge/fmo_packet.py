# -*- coding: utf-8 -*-
"""FMO 语音消息包封装（严格按官方《FMO 语音数据开放协议》）。

层次：消息包(≤1400B) → 传输帧 ×N → 编码语音帧 → 编码载荷(OPUS/RADPCM)
所有多字节字段 **小端序**。CRC32 仅覆盖「帧区」（消息头之后的部分）。
"""
import struct
import zlib

MSG_HEADER_LEN = 64
TF_HEADER_LEN = 8
CVF_HEADER_LEN = 8
MTU = 1400
AGG_MS = 250

CM_PCM, CM_OPUS, CM_RADPCM = 0, 1, 2
DEFAULT_VENDOR = 0x2001          # 软件区 0x2000~0x2FFF（免注册）
VERSION = 1

# 编码帧头里 compressMode(1)+len(2) 之后的 5 字节。
# 官方参考实现（NOCALL 实测，731 帧全部一致）填 0x3D1400E03D，不是零。
# 实测自 EMQX trace 的真实 FMO/RAW 报文。
CODEC_TAG = bytes.fromhex("3d1400e03d")


def build_voice_packet(uid, callsign, frames, stream_begin_utc=None, ts_ms=None,
                       srv_uid=0, vendor=DEFAULT_VENDOR, smeter=0, seq_index0=1):
    """frames: [(compressMode, payload_bytes), ...]  → 完整消息包 bytes

    · 消息头 64B：version(2) vendor(4) UID(4) callsign(12) streamBeginUTC(4)
                  timestamp(4) len(4) frameNum(2) checkSum(4) smeter(1) srvUID(4) reserved(19)
    · 传输帧 8B：index(2, 从1递增) len(2,含头) reserved(4)  + 编码语音帧
    · 编码语音帧 8B：compressMode(1) len(2,含头) reserved(5) + 载荷
    """
    if not ts_ms:
        import time as _t
        ts_ms = int(_t.time() * 1000)
    if stream_begin_utc is None:
        stream_begin_utc = ts_ms

    frame_area = bytearray()
    for i, (cm, payload) in enumerate(frames):
        cvf_len = CVF_HEADER_LEN + len(payload)
        cvf = bytearray()
        cvf += bytes([cm & 0xFF])
        cvf += struct.pack("<H", cvf_len)
        cvf += CODEC_TAG                       # 参考实现填 0x3D1400E03D，不是零
        cvf += payload

        tf_len = TF_HEADER_LEN + cvf_len
        frame_area += struct.pack("<HH", (seq_index0 + i) & 0xFFFF, tf_len)
        frame_area += b"\x00" * 4
        frame_area += cvf

    callsign_bytes = callsign.upper().encode("ascii")[:12].ljust(12, b"\x00")
    total_len = MSG_HEADER_LEN + len(frame_area)
    hdr = bytearray()
    hdr += struct.pack("<H", VERSION)
    hdr += struct.pack("<I", vendor & 0xFFFFFFFF)
    hdr += struct.pack("<I", uid & 0xFFFFFFFF)
    hdr += callsign_bytes
    hdr += struct.pack("<I", stream_begin_utc & 0xFFFFFFFF)
    hdr += struct.pack("<I", ts_ms & 0xFFFFFFFF)
    hdr += struct.pack("<I", total_len)
    hdr += struct.pack("<H", len(frames))
    hdr += struct.pack("<I", zlib.crc32(bytes(frame_area)) & 0xFFFFFFFF)
    hdr += struct.pack("<B", smeter & 0xFF)
    hdr += struct.pack("<I", srv_uid & 0xFFFFFFFF)
    hdr += b"\x00" * 19
    assert len(hdr) == MSG_HEADER_LEN, len(hdr)
    return bytes(hdr) + bytes(frame_area)


def parse_voice_packet(data: bytes):
    """返回 dict（含 frames=[(compressMode, payload), ...]）"""
    if len(data) < MSG_HEADER_LEN:
        raise ValueError("packet too short: %d" % len(data))
    (version, vendor, uid) = struct.unpack_from("<HII", data, 0)
    callsign = data[10:22].split(b"\x00", 1)[0].decode("ascii", "ignore")
    (stream_begin, ts_ms, total_len) = struct.unpack_from("<III", data, 22)
    frame_num = struct.unpack_from("<H", data, 34)[0]
    check = struct.unpack_from("<I", data, 36)[0]
    smeter = data[40]
    srv_uid = struct.unpack_from("<I", data, 41)[0]

    frame_area = data[MSG_HEADER_LEN:total_len]
    frames = []
    off = 0
    while off + TF_HEADER_LEN <= len(frame_area):
        index, tf_len = struct.unpack_from("<HH", frame_area, off)
        cv = frame_area[off + TF_HEADER_LEN: off + tf_len]
        if len(cv) < CVF_HEADER_LEN:
            break
        cm = cv[0]
        cv_len = struct.unpack_from("<H", cv, 1)[0]
        payload = cv[CVF_HEADER_LEN:cv_len]
        frames.append((cm, payload))
        off += tf_len
    return {
        "version": version, "vendor": vendor, "uid": uid, "callsign": callsign,
        "streamBeginUTC": stream_begin, "timestamp": ts_ms, "len": total_len,
        "frameNum": frame_num, "checkSum": check, "smeter": smeter, "srvUID": srv_uid,
        "frames": frames, "crc_ok": (zlib.crc32(frame_area) & 0xFFFFFFFF) == check,
    }


def chunk_frames(payload_frames, max_packet=MTU):
    """按 MTU 把若干「编码语音帧」切成多个消息包（每帧已含 8B 帧头+载荷）。"""
    out, cur, sz = [], [], MSG_HEADER_LEN
    for cm, payload in payload_frames:
        add = TF_HEADER_LEN + CVF_HEADER_LEN + len(payload)
        if cur and sz + add > max_packet:
            out.append(cur); cur, sz = [], MSG_HEADER_LEN
        cur.append((cm, payload)); sz += add
    if cur:
        out.append(cur)
    return out
