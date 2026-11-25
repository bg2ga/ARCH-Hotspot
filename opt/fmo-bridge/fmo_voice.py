# -*- coding: utf-8 -*-
"""FMO 语音编解码：OPUS（40ms/8kHz/单声道，compressMode=1）与 RADPCM（IMA ADPCM，80ms，compressMode=2）。
   参数严格按官方《FMO 语音数据开放协议》。OPUS 走 ctypes 直调 libopus0，无需 pip。
"""
import ctypes
import ctypes.util

SAMPLE_RATE = 8000
OPUS_FRAME = 320          # 40ms @8kHz
RADPCM_FRAME = 640        # 80ms @8kHz

OPUS_APPLICATION_VOIP = 2048
OPUS_SIGNAL_VOICE = 3001
OPUS_SET_BITRATE = 4002
OPUS_SET_VBR = 4006
OPUS_SET_COMPLEXITY = 4010
OPUS_SET_SIGNAL = 4024
OPUS_AUTO = -1000


class OpusCodec:
    def __init__(self):
        path = ctypes.util.find_library("opus") or "libopus.so.0"
        self.lib = ctypes.CDLL(path)
        L = self.lib
        L.opus_encoder_create.restype = ctypes.c_void_p
        L.opus_encoder_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          ctypes.POINTER(ctypes.c_int)]
        L.opus_decoder_create.restype = ctypes.c_void_p
        L.opus_decoder_create.argtypes = [ctypes.c_int, ctypes.c_int,
                                          ctypes.POINTER(ctypes.c_int)]
        L.opus_encode.restype = ctypes.c_int
        L.opus_encode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16),
                                  ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte),
                                  ctypes.c_int]
        L.opus_decode.restype = ctypes.c_int
        L.opus_decode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte),
                                  ctypes.c_int, ctypes.POINTER(ctypes.c_int16),
                                  ctypes.c_int, ctypes.c_int]
        L.opus_encoder_ctl.restype = ctypes.c_int

        err = ctypes.c_int(0)
        self.enc = L.opus_encoder_create(SAMPLE_RATE, 1, OPUS_APPLICATION_VOIP,
                                         ctypes.byref(err))
        assert err.value == 0, "opus_encoder_create err=%d" % err.value
        L.opus_encoder_ctl(self.enc, OPUS_SET_BITRATE, OPUS_AUTO)
        L.opus_encoder_ctl(self.enc, OPUS_SET_VBR, 1)
        L.opus_encoder_ctl(self.enc, OPUS_SET_COMPLEXITY, 4)
        L.opus_encoder_ctl(self.enc, OPUS_SET_SIGNAL, OPUS_SIGNAL_VOICE)
        err = ctypes.c_int(0)
        self.dec = L.opus_decoder_create(SAMPLE_RATE, 1, ctypes.byref(err))
        assert err.value == 0, "opus_decoder_create err=%d" % err.value

    def encode(self, pcm_bytes: bytes) -> bytes:
        """pcm_bytes: 320 样本 S16LE（640 字节）→ 约 40-60 字节 OPUS 帧"""
        n = len(pcm_bytes) // 2
        src = (ctypes.c_int16 * n).from_buffer_copy(pcm_bytes)
        out = (ctypes.c_ubyte * 512)()
        k = self.lib.opus_encode(self.enc, src, n, out, 512)
        if k < 0:
            raise RuntimeError("opus_encode err=%d" % k)
        return bytes(out[:k])

    def decode(self, opus_frame: bytes, frame_size: int = OPUS_FRAME) -> bytes:
        if not opus_frame:
            return b"\x00" * (frame_size * 2)
        src = (ctypes.c_ubyte * len(opus_frame)).from_buffer_copy(opus_frame)
        out = (ctypes.c_int16 * frame_size)()
        k = self.lib.opus_decode(self.dec, src, len(opus_frame), out, frame_size, 0)
        if k < 0:
            raise RuntimeError("opus_decode err=%d" % k)
        return bytes(bytearray(out))[:k * 2]

    def __del__(self):
        try:
            self.lib.opus_encoder_destroy(self.enc)
            self.lib.opus_decoder_destroy(self.dec)
        except Exception:
            pass


# ============================================================ RADPCM (IMA ADPCM)
STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024,
    3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493,
    10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767,
]
INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]
_R = 32735          # Q15 0.999 直流抑制系数（截止约 1.3Hz）


class RadPcmCodec:
    """一帧 = 640 样本(80ms,1280B PCM) → 320B ADPCM，状态跨帧连续，带帧恢复点。"""

    def __init__(self):
        self.predictor = 0
        self.index = 0
        self.prev_x = 0
        self.prev_y = 0
        self.frame_index = 0

    def _dc(self, x):
        y = x - self.prev_x + ((_R * self.prev_y) >> 15)
        y = max(-32768, min(32767, y))
        self.prev_x, self.prev_y = x, y
        return y

    def _enc_sample(self, s, step_idx):
        step = STEP_TABLE[step_idx]
        diff = s - self.predictor
        code = 0
        if diff < 0:
            code = 8
            diff = -diff
        delta = step >> 3
        if diff >= step:
            code |= 4; diff -= step; delta += step
        step >>= 1
        if diff >= step:
            code |= 2; diff -= step; delta += step
        step >>= 1
        if diff >= step:
            code |= 1; delta += step
        if code & 8:
            self.predictor -= delta
        else:
            self.predictor += delta
        self.predictor = max(-32768, min(32767, self.predictor))
        step_idx = max(0, min(88, step_idx + INDEX_TABLE[code]))
        return step_idx, code

    def encode(self, pcm_bytes: bytes) -> bytes:
        n = len(pcm_bytes) // 2
        samples = [int.from_bytes(pcm_bytes[i * 2:i * 2 + 2], "little", signed=True)
                   for i in range(n)]
        recov, recov_idx = self.predictor, self.index
        out = bytearray()
        for i in range(0, n, 2):
            self.index, hi = self._enc_sample(self._dc(samples[i]), self.index)
            self.index, lo = self._enc_sample(self._dc(samples[i + 1]), self.index)
            out.append((hi << 4) | lo)
        hdr = bytearray()
        hdr += (self.frame_index & 0xFFFF).to_bytes(2, "little")
        hdr += (recov & 0xFFFF).to_bytes(2, "little")
        hdr += bytes([recov_idx & 0xFF, 0])
        hdr += (320).to_bytes(2, "little")
        self.frame_index = (self.frame_index + 1) & 0xFFFF
        return bytes(hdr) + bytes(out)

    def decode(self, frame: bytes) -> bytes:
        if len(frame) < 8:
            return b"\x00" * (RADPCM_FRAME * 2)
        fi = int.from_bytes(frame[0:2], "little")
        recov = int.from_bytes(frame[2:4], "little", signed=True)
        step_idx = frame[4]
        # adpcmBytes 兼容性（官方协议 §5.2 注记）：早期实现是 8-bit 字段，
        # 线值被截断成 64，且高字节是未初始化填充。故一律以「帧实际长度」为准，
        # 即固定的 320 字节；仅当帧长异常时才回退到低字节判定。
        data_len = len(frame) - 8
        if data_len <= 0 or data_len > 320:
            data_len = frame[6]
            if data_len == 0 or data_len > 320:
                data_len = 320
        if fi == 0 or fi != (self.frame_index & 0xFFFF):
            self.predictor, self.index = recov, step_idx      # 帧恢复点
        out = bytearray()
        for b in frame[8:8 + data_len]:
            for code in ((b >> 4) & 0xF, b & 0xF):
                step = STEP_TABLE[self.index]
                delta = step >> 3
                if code & 4: delta += step
                if code & 2: delta += step >> 1
                if code & 1: delta += step >> 2
                if code & 8:
                    self.predictor -= delta
                else:
                    self.predictor += delta
                self.predictor = max(-32768, min(32767, self.predictor))
                self.index = max(0, min(88, self.index + INDEX_TABLE[code]))
                out += int(self.predictor).to_bytes(2, "little", signed=True)
        self.frame_index = (fi + 1) & 0xFFFF
        return bytes(out)

