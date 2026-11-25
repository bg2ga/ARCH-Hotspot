# -*- coding: utf-8 -*-
"""下行音频整形（可开关 / 可调参）。

背景：FMO 网络语音是低码率编码（RADPCM 4bit IMA ADPCM 或 OPUS），解码出来的
PCM 带明显量化噪声和低频隆隆声。直接送 FM 调制时听感偏「沙 / 哑」。
这里做四件温和的事，全部可关：

  1) 高通去隆隆（默认 180Hz）—— 低频对可懂度贡献很小，却白占频偏
  2) 高频临场补偿（默认 +2.5dB @ 1.8kHz 一阶搁架）—— 抵消链路/手台去加重的钝感
  3) 软限幅（默认 -1.5dBFS）—— 保证不撞 Host 里 12bit 的硬上限
  4) 向下扩展「噪声门」（默认关）—— 压制音节之间的编码底噪

所有处理都是流式的（保留滤波器状态），可以逐 20ms 块调用。
"""
import math

S16_MAX = 32767.0


def make_tone_pcm(freq=1000.0, ms=200, gain=0.5, sr=8000, fade_ms=8):
    """生成一段正弦确认音（尾音），带短淡入淡出避免起止爆音。"""
    n = int(sr * ms / 1000.0)
    nf = max(1, int(sr * fade_ms / 1000.0))
    out = bytearray()
    amp = max(0.0, min(1.0, gain)) * S16_MAX
    for i in range(n):
        v = math.sin(2.0 * math.pi * freq * i / sr) * amp
        # 淡入淡出
        if i < nf:
            v *= i / float(nf)
        elif i > n - nf:
            v *= (n - i) / float(nf)
        iv = int(v)
        iv = max(-32768, min(32767, iv))
        out += int(iv).to_bytes(2, "little", signed=True)
    return bytes(out)


class DownlinkShaper:
    """下行 PCM 整形器。参数全部可调，默认都是温和值。"""

    def __init__(self, sr=8000, cfg=None):
        c = cfg or {}
        self.enabled = bool(c.get("enable", True))
        self.sr = sr

        # 1) 一阶高通：y[n] = a*(y[n-1] + x[n] - x[n-1])
        self.hpf_hz = float(c.get("hpf_hz", 180.0))
        if self.hpf_hz > 0:
            self._a_hp = 1.0 / (1.0 + 2.0 * math.pi * self.hpf_hz / sr)
        else:
            self._a_hp = 0.0

        # 2) 一阶高频搁架：y = x + g*(x - lopass(x))
        self.shelf_db = float(c.get("shelf_db", 2.5))
        self.shelf_hz = float(c.get("shelf_hz", 1800.0))
        if self.shelf_hz > 0:
            self._a_lp = 1.0 - math.exp(-2.0 * math.pi * self.shelf_hz / sr)
        else:
            self._a_lp = 1.0
        self._g_shelf = 10.0 ** (self.shelf_db / 20.0) - 1.0

        # 3) 软限幅
        self.limit = float(c.get("limit", 0.965))
        self.peak_ceiling = max(0.10, min(0.99, float(c.get("peak_ceiling", 0.99))))
        self.peak_hits = 0

        # 2.5) AGC —— 音频自动增益控制
        #   实测：FMO 网络下行整体 RMS 达 56% FS、近 5% 样点贴顶 →
        #   听感"过载"。AGC 把 RMS 拉平到 agc_target，峰值交给限幅器兜底。
        self.agc_on = bool(c.get("agc", True))
        self.agc_target = float(c.get("agc_target", 0.09))          # 目标 RMS（占满幅比例）
        self.agc_gmax = 10.0 ** (float(c.get("agc_max_gain_db", 20.0)) / 20.0)
        self.agc_gmin = 10.0 ** (float(c.get("agc_min_gain_db", -26.0)) / 20.0)
        self.agc_gate = float(c.get("agc_gate", 0.004))             # 低于此视为静音，冻结增益
        rms_ms = float(c.get("agc_rms_ms", 120.0))                  # RMS 观察窗
        dn_ms = float(c.get("agc_down_ms", 12.0))                   # 增益下降（压）速度
        up_ms = float(c.get("agc_up_ms", 600.0))                    # 增益回升（抬）速度

        def _a(ms):
            return 1.0 - math.exp(-1.0 / max(1.0, sr * ms / 1000.0))

        self._a_rms = _a(rms_ms)
        self._a_gdn = _a(dn_ms)
        self._a_gup = _a(up_ms)
        self._rms = 0.0        # 平方域 RMS
        self._g = 1.0          # 当前增益
        # 诊断统计
        self.pk_in = 0.0
        self.pk_out = 0.0
        self.rms_acc = 0.0
        self.n_acc = 0
        self.g_min = 9.9
        self.g_max = 0.0
        self.clip_in = 0

        # 4) 向下扩展（噪声门）：低于 thr 的部分按 ratio 压低
        self.exp_on = bool(c.get("expander", False))
        self.exp_thr = float(c.get("expander_thr", 0.010))   # ≈ -40dBFS
        self.exp_ratio = float(c.get("expander_ratio", 3.0))

        # 状态
        self._x1 = 0.0
        self._y1 = 0.0
        self._lp = 0.0
        self._env = 0.0
        self._g = 1.0
        self._g2 = 1.0          # 向下扩展用的增益（与 AGC 的 _g 分开）
        self.calls = 0

    # ------------------------------------------------------------------
    def stats(self):
        """返回本段统计（供桥接每 30s 打印，便于判断 AGC 是否工作正常）"""
        rms = math.sqrt(self.rms_acc / self.n_acc) if self.n_acc else 0.0
        return {"pk_in": self.pk_in, "pk_out": self.pk_out, "rms_out": rms,
                "gain_now": self._g, "gain_min": self.g_min if self.g_min < 9.0 else 1.0,
                "gain_max": self.g_max, "clip": self.clip_in, "peak_cap": self.peak_hits, "n": self.n_acc}

    def reset_stats(self):
        self.pk_in = self.pk_out = 0.0
        self.rms_acc = 0.0
        self.n_acc = 0
        self.clip_in = 0
        self.peak_hits = 0
        self.g_min = 9.9
        self.g_max = 0.0

    # ------------------------------------------------------------------
    def process(self, pcm):
        """输入/输出都是 S16LE 字节串。"""
        if not self.enabled or not pcm:
            return pcm
        n = len(pcm) // 2
        vals = [int.from_bytes(pcm[i * 2:i * 2 + 2], "little", signed=True)
                for i in range(n)]
        out = bytearray(len(pcm))
        a_hp, a_lp = self._a_hp, self._a_lp
        g_sh, lim = self._g_shelf, self.limit
        agc_on = self.agc_on
        a_rms, a_gdn, a_gup = self._a_rms, self._a_gdn, self._a_gup
        tgt, gmax, gmin = self.agc_target, self.agc_gmax, self.agc_gmin
        gate2 = self.agc_gate * self.agc_gate

        for i in range(n):
            x = vals[i] / S16_MAX
            if x > self.pk_in:
                self.pk_in = x
            elif -x > self.pk_in:
                self.pk_in = -x

            # 1) 高通
            if a_hp > 0.0:
                y = a_hp * (self._y1 + x - self._x1)
                self._x1, self._y1 = x, y
                x = y

            # 2) 高频搁架
            self._lp += a_lp * (x - self._lp)
            if g_sh != 0.0:
                x = x + g_sh * (x - self._lp)

            # 2.5) AGC：把音量拉平（对方大声不再过载，小声也能听清）
            if agc_on:
                self._rms += a_rms * (x * x - self._rms)
                if self._rms > gate2:
                    want = tgt / math.sqrt(self._rms)
                    if want > gmax:
                        want = gmax
                    elif want < gmin:
                        want = gmin
                    # 增益要压得快、抬得慢，避免"抽吸"感
                    self._g += (a_gdn if want < self._g else a_gup) * (want - self._g)
                    if self._g < self.g_min:
                        self.g_min = self._g
                    if self._g > self.g_max:
                        self.g_max = self._g
                # Peak-aware AGC ceiling: never let a computed AGC gain push
                # an instantaneous peak past the soft-limiter headroom.
                if x != 0.0:
                    _peak_cap = self.peak_ceiling / abs(x)
                    if self._g > _peak_cap:
                        self._g = _peak_cap
                        self.peak_hits += 1
                x *= self._g

            # 4) 向下扩展
            if self.exp_on:
                a = abs(x)
                self._env = a if a > self._env else (0.995 * self._env + 0.005 * a)
                if self._env < self.exp_thr:
                    tgtg = (self._env / self.exp_thr) ** (self.exp_ratio - 1.0)
                    self._g2 += 0.02 * (tgtg - self._g2)
                else:
                    self._g2 += 0.20 * (1.0 - self._g2)
                x *= self._g2

            # 3) 软限幅（在 ±limit 内线性，越界平滑压到 limit）
            a = abs(x)
            if a >= lim:
                self.clip_in += 1
            if a > lim:
                over = a - lim
                x = (1.0 if x > 0 else -1.0) * (lim + (1.0 - lim) * (1.0 - math.exp(-over / (1.0 - lim))))
            if x > self.pk_out:
                self.pk_out = x
            elif -x > self.pk_out:
                self.pk_out = -x
            self.rms_acc += x * x
            self.n_acc += 1
            iv = int(x * S16_MAX)
            iv = max(-32768, min(32767, iv))
            out[i * 2:i * 2 + 2] = iv.to_bytes(2, "little", signed=True)

        self.calls += 1
        return bytes(out)
