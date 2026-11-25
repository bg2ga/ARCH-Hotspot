#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 ARCH 多信道配置并重载 MMDVM-Multi。"""
import configparser
import io
import copy
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

PLAN = "/etc/mmdvm/channels.json"
MULTI = "/etc/MMDVM-Multi.ini"
CHANNEL_BINS = "/etc/mmdvm/channel-bins"
IQ_BASE = "/etc/MMDVM-IQ.ini"
HOST_BASE = "/etc/MMDVM-Host.ini"
LIMIT_SCRIPT = "/usr/local/libexec/arch-channel-limit.py"
MODES = ("DMR", "D-STAR", "YSF", "P25", "NXDN", "FM", "POCSAG")

MODE_SECTIONS = {
    "DMR": ("DMR", "DMR Network"),
    "D-STAR": ("D-Star", "D-Star Network"),
    "YSF": ("System Fusion", "System Fusion Network"),
    "P25": ("P25", "P25 Network"),
    "NXDN": ("NXDN", "NXDN Network"),
    "FM": ("FM", "FM Network"),
    "POCSAG": ("POCSAG", "POCSAG Network"),
}
SYSTEM_KEYS = {
    "ENABLE", "ADDRESS", "GATEWAYADDRESS", "GATEWAYPORT", "LOCALPORT",
    "MODEMPORT", "RPPORT", "PORT", "RXFREQUENCY", "TXFREQUENCY",
    "CALLSIGN", "ID", "NAME", "DESCRIPTION", "URL",
    # 下面两个是由通道/网关配置算出来的“系统键”，不能当作可保留的用户设置：
    # 随包 MMDVM-Host-N.ini 模板里每个通道预开了一个模式，快照会把模板里的旧值
    # （NXDN Protocol=Kenwood、D-Star Module=B）在 mode_flags 之后又写回去，
    # 导致 Host 与网关的协议/模块对不上 -> NXDN 能听不能说、D-STAR 模块不匹配。
    "PROTOCOL", "MODULE",
}



def ini(path):
    cp = configparser.RawConfigParser()
    cp.optionxform = str
    try: cp.read(path, encoding="utf-8")
    except Exception: pass
    return cp


def save(cp, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for sec in cp.sections():
            f.write("[" + sec + "]\n")
            for k, v in cp.items(sec, raw=True): f.write(str(k) + "=" + str(v) + "\n")
    os.replace(tmp, path)


def setv(cp, sec, key, val):
    if not cp.has_section(sec): cp.add_section(sec)
    cp.set(sec, key, str(val))


def hardware_limit():
    try:
        r=subprocess.run([LIMIT_SCRIPT],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=8)
        return max(1,min(7,int((r.stdout or "7").strip())))
    except Exception:
        return 7


def default_plan():
    try:
        with open(PLAN, encoding="utf-8") as f: d=json.load(f)
        if isinstance(d, dict) and d.get("list"): return d
    except Exception: pass
    return {"channels": 1, "center_rx": 434500000, "center_tx": 439500000,
            "callsign": "", "dmr_id": "", "spacing": 25000, "sample_rate": 150000,
            "list": [{"n": i, "mode": "NONE",
                      "rx": 434500000 + grid_step(i, 150000) * 25000,
                      "tx": 439500000 + grid_step(i, 150000) * 25000} for i in range(1, 12)]}


def grid_step(i, sr=300000):
    n=max(1, round(int(sr or 300000)/25000.0))
    m=max(1, min(int(n/2)-1, 4))
    p=i-1
    b=p if p<m else (n-1)-(p-m)
    return b if b*2<=n else b-n


def choose_center(freqs, sr, preferred_center=None):
    """Choose a common center and distinct PFB bins for absolute frequencies."""
    freqs = [int(f) for f in freqs]
    n = max(1, int(round(int(sr) / 25000.0)))
    kmin, kmax = -(n // 2), n // 2
    candidates = set()
    for f in freqs:
        for k in range(kmin, kmax + 1):
            candidates.add(f - k * 25000)
    if preferred_center:
        candidates.add(int(preferred_center))
    best = None
    for center in candidates:
        bins, errs = [], []
        ok = True
        for f in freqs:
            k = int(round((f - center) / 25000.0))
            if k < kmin or k > kmax:
                ok = False
                break
            err = abs(f - center - k * 25000)
            b = k % n
            if b in bins:
                ok = False
                break
            bins.append(b)
            errs.append(err)
        if not ok or max(errs) > 1000:
            continue
        dist = abs(center - int(preferred_center)) if preferred_center else 0
        mid = int(sum(freqs) / len(freqs))
        score = (max(errs), sum(errs), dist, abs(center - mid))
        if best is None or score < best[0]:
            best = (score, center, bins)
    if best is None:
        lo = min(freqs); hi = max(freqs)
        raise RuntimeError(
            "频率不满足当前采样率的 25 kHz 子带栅格，或子带数量不足。"
            "当前 %d Hz 采样率可用栅格范围约为 %.4f～%.4f MHz；请调整中心/每路频率或增加采样率。" %
            (int(sr), (lo - 75000) / 1e6, (hi + 75000) / 1e6))
    return best[1], best[2]


SUPPORTED_RATES = (50000, 75000, 150000, 300000, 600000)

def validate_sample_rate(rate, channels):
    rate = int(rate)
    channels = int(channels)
    if rate not in SUPPORTED_RATES:
        raise RuntimeError("不支持的采样率 %d；SXceiver 可用档位为 50000/75000/150000/300000/600000 Hz" % rate)
    bins = rate // 25000
    if channels >= bins:
        raise RuntimeError("采样率 %d Hz 只能支持少于 %d 个活动通道；当前配置为 %d 路" % (rate, bins, channels))
    return rate


def auto_sample_rate(channels):
    # Hard project rule: <=5 RF channels use 150 kHz; >5 use 300 kHz.
    return 300000 if int(channels) > 5 else 150000


def normalized(plan):
    d=default_plan(); d.update(plan or {})
    limit=hardware_limit()
    ch=max(1,min(limit,int(d.get("channels") or 1)))
    d["channels"]=ch
    if d.get("auto_sample_rate", True):
        sr=auto_sample_rate(ch)
    else:
        sr=int(d.get("sample_rate") or 150000)
    sr=validate_sample_rate(sr, ch)
    rx_pref=int(d.get("center_rx") or 434500000); tx_pref=int(d.get("center_tx") or 439500000)
    old={int(x.get("n",0)):x for x in d.get("list",[]) if str(x.get("n", "")).isdigit()}
    out=[]; rx_freqs=[]; tx_freqs=[]
    for i in range(1,12):
        x=dict(old.get(i) or {})
        x["n"]=i
        x["mode"]=str(x.get("mode") or "NONE").upper()
        if x["mode"] not in MODES+("NONE",): x["mode"]="NONE"
        drx=int(rx_pref+grid_step(i,sr)*25000)
        dtx=int(tx_pref+grid_step(i,sr)*25000)
        x["rx"]=int(x.get("rx") or drx)
        x["tx"]=int(x.get("tx") or dtx)
        if i <= ch:
            rx_freqs.append(x["rx"]); tx_freqs.append(x["tx"])
        out.append(x)
    rx_center, rx_bins = choose_center(rx_freqs, sr, rx_pref)
    tx_center, tx_bins = choose_center(tx_freqs, sr, tx_pref)
    for i in range(ch+1,12):
        out[i-1]["rx"]=int(rx_center+grid_step(i,sr)*25000)
        out[i-1]["tx"]=int(tx_center+grid_step(i,sr)*25000)
    d["list"]=out[:max(1,ch)]; d["sample_rate"]=sr; d["auto_sample_rate"]=bool(d.get("auto_sample_rate", True))
    d["center_rx"]=rx_center; d["center_tx"]=tx_center
    d["rx_bins"]=rx_bins; d["tx_bins"]=tx_bins
    d["hardware_max"]=limit
    return d


def set_ini_text_value(path, section, key, value):
    """Line-based INI editor that keeps DMRGateway.ini formatting intact."""
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return False
    start = None
    end = len(lines)
    sec_l = section.lower()
    for i, line in enumerate(lines):
        m = re.match(r'^\s*\[([^\]]+)\]\s*$', line)
        if not m:
            continue
        if start is None and m.group(1).strip().lower() == sec_l:
            start = i
            continue
        if start is not None:
            end = i
            break
    if start is None:
        if lines and lines[-1] != '':
            lines.append('')
        lines.append('[%s]' % section)
        lines.append('%s=%s' % (key, value))
    else:
        pat = re.compile(r'^\s*' + re.escape(key) + r'\s*=')
        for i in range(start + 1, end):
            if pat.match(lines[i]):
                lines[i] = '%s=%s' % (key, value)
                break
        else:
            lines.insert(end, '%s=%s' % (key, value))
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return True


def sync_dmr_gateway(plan):
    """Keep every DMRGateway instance aligned with its own RF channel."""
    for item in (plan.get('list') or [])[:int(plan.get('channels') or 0)]:
        if str(item.get('mode', '')).upper() != 'DMR':
            continue
        try:
            n = int(item.get('n'))
        except Exception:
            continue
        path = '/etc/DMRGateway.ini' if n == 1 else '/etc/DMRGateway-%d.ini' % n
        if n > 1 and not os.path.exists(path):
            try:
                shutil.copy2('/etc/DMRGateway.ini', path)
            except Exception:
                continue
        set_ini_text_value(path, 'General', 'LocalPort', str(62031 + (n - 1) * 10))
        set_ini_text_value(path, 'General', 'RptPort', str(62032 + (n - 1) * 10))

def capture_user_settings():
    """Snapshot user-editable values from existing Host files before rebuilding them."""
    profiles = {m: {} for m in MODE_SECTIONS}
    general = {}
    try:
        for path in sorted(Path('/etc').glob('MMDVM-Host-*.ini')):
            cp = ini(str(path))
            active = None
            for mode, sections in MODE_SECTIONS.items():
                sec = sections[0]
                if cp.has_section(sec) and cp.get(sec, 'Enable', fallback='0').strip() == '1':
                    active = mode
                    break
            if active is None:
                continue
            for sec in MODE_SECTIONS[active]:
                if not cp.has_section(sec):
                    continue
                for k, v in cp.items(sec, raw=True):
                    if k.upper() in SYSTEM_KEYS:
                        continue
                    profiles[active].setdefault(sec, {})[k] = v
            if cp.has_section('General'):
                for k, v in cp.items('General', raw=True):
                    if k.upper() not in SYSTEM_KEYS:
                        general[k] = v
        # (已删除) 旧的 FM 劣化迁移：强制 ExtAudioBoost=2 / FMTXLevel=82
    except Exception:
        pass
    return profiles, general

def _dmr_id_valid(x):
    x = str(x or "").strip()
    return bool(x) and any(c in "123456789" for c in x)


def resolve_dmr_id(plan_dmr_id):
    """channels.json 的 dmr_id 为空/全 0 时，回退到 DMRGateway.ini / MMDVM-Host.ini
    里已保存的 Id —— 与 sys-manage.sh 的 _hotspot_heal 判定保持一致，
    避免出现"网关已连上服务器、但热点 DMR 解码被关掉"。
    返回 (有效ID 或 原值)。"""
    if _dmr_id_valid(plan_dmr_id):
        return str(plan_dmr_id).strip()
    for path, sec, key in (("/etc/DMRGateway.ini", "General", "Id"),
                           ("/etc/MMDVM-Host.ini", "General", "Id"),
                           ("/etc/MMDVM-Host-1.ini", "General", "Id")):
        try:
            _cp = ini(path)
            _v = _cp.get(sec, key, fallback="").strip()
            if _dmr_id_valid(_v):
                return _v
        except Exception:
            pass
    return str(plan_dmr_id or "").strip()


def _call_valid(x):
    x = str(x or "").strip().upper()
    return bool(x) and x not in ("NOCALL", "NONE", "0")


def resolve_callsign(plan_callsign):
    """plan 的 callsign 为空/NOCALL 时，回退到 DMRGateway.ini / MMDVM-Host.ini 里已保存的呼号。"""
    if _call_valid(plan_callsign):
        return str(plan_callsign).strip().upper()
    for path, sec, key in (("/etc/DMRGateway.ini", "General", "Callsign"),
                           ("/etc/MMDVM-Host.ini", "General", "Callsign"),
                           ("/etc/MMDVM-Host-1.ini", "General", "Callsign")):
        try:
            _cp = ini(path)
            _v = _cp.get(sec, key, fallback="").strip()
            if _call_valid(_v):
                return _v.upper()
        except Exception:
            pass
    return str(plan_callsign or "").strip().upper()


_PLACEHOLDERS = ("", "NOCALL", "NONE", "0", "0000000", "00000000", "000000000")


def _fill_placeholder(path, section, key, value, flat=False):
    """只在目标处仍是占位值时填入 value；用户已填过的值不覆盖。"""
    if not value:
        return False
    try:
        if flat:
            lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines(True)
            out = []
            done = False
            for ln in lines:
                s = ln.strip()
                if (not done) and s.split("=", 1)[0].strip() == key:
                    cur = s.split("=", 1)[1].strip() if "=" in s else ""
                    if cur in _PLACEHOLDERS:
                        ln = "%s=%s\n" % (key, value)
                        done = True
                    else:
                        done = True
                out.append(ln)
            if not done:
                out.append("%s=%s\n" % (key, value))
            Path(path).write_text("".join(out), encoding="utf-8")
            return True
        cp = ini(path)
        if not cp.has_section(section):
            return False
        cur = cp.get(section, key, fallback="").strip()
        if cur in _PLACEHOLDERS:
            cp.set(section, key, str(value))
            save(cp, path)
            return True
    except Exception:
        return False
    return False


def propagate_gateway_identity(callsign, dmr_id):
    """把全局呼号/ID 补进各模式网关配置的占位处（只补空，不覆盖用户已填）。"""
    if _call_valid(callsign):
        # ircDDBGateway 的模块呼号（repeaterCall1）为空时补成 “呼号 + 模块字母”
        try:
            _mod = _dstar_module()
            _lines = Path("/etc/ircddbgateway").read_text(encoding="utf-8", errors="replace").splitlines()
            _done = False
            for _i, _ln in enumerate(_lines):
                if _ln.split("=", 1)[0].strip() == "repeaterCall1":
                    if _ln.split("=", 1)[1].strip() in _PLACEHOLDERS:
                        _lines[_i] = "repeaterCall1=%s %s" % (callsign, _mod)
                    _done = True
                    break
            if not _done:
                _lines.append("repeaterCall1=%s %s" % (callsign, _mod))
            Path("/etc/ircddbgateway").write_text("\n".join(_lines) + "\n", encoding="utf-8")
        except Exception:
            pass
        _fill_placeholder("/etc/YSFGateway.ini", "General", "Callsign", callsign)
        _fill_placeholder("/etc/P25Gateway.ini", "General", "Callsign", callsign)
        _fill_placeholder("/etc/NXDNGateway.ini", "General", "Callsign", callsign)
        _fill_placeholder("/etc/ircddbgateway", "", "gatewayCallsign", callsign, flat=True)
    if _dmr_id_valid(dmr_id):
        _fill_placeholder("/etc/YSFGateway.ini", "General", "Id", dmr_id)
        _fill_placeholder("/etc/NXDNGateway.ini", "General", "Id", dmr_id)
    return


def _nxdn_protocol():
    """Host [NXDN Network] Protocol 必须与 NXDNGateway 的 RptProtocol 一致，
    否则 MMDVMHost 与 NXDNGateway 之间收 / 发都对不上（表现为能听不能说）。"""
    try:
        _cp = ini("/etc/NXDNGateway.ini")
        _p = _cp.get("General", "RptProtocol", fallback="").strip()
        if _p:
            return _p
    except Exception:
        pass
    return "Kenwood"


def _dstar_module():
    """Host [D-Star] Module 必须与 ircddbgateway 的 repeaterBand1 一致，
    否则 D-STAR 模块对不上（改模块后收 / 发都会失败）。"""
    try:
        for ln in io.open("/etc/ircddbgateway", encoding="utf-8", errors="replace"):
            if ln.strip().startswith("repeaterBand1="):
                v = ln.split("=", 1)[1].strip().upper()[:1]
                if v.isalpha():
                    return v
    except Exception:
        pass
    return "B"


def _ysf_keepalive():
    """YSFGateway 配置里 InactivityTimeout>0 会让选好的房间在空闲后自动断开，
    而 Reconnect=0 又不会自动连回来（表现为"过十几分钟自己掉，变成未选择"）。
    这里强制关闭空闲断开并开启自动重连。"""
    _p = "/etc/YSFGateway.ini"
    try:
        _lines = Path(_p).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    _want = {"inactivitytimeout": "InactivityTimeout=0", "reconnect": "Reconnect=1"}
    _seen = set()
    for _i, _ln in enumerate(_lines):
        _k = _ln.split("=", 1)[0].strip().lower() if "=" in _ln else ""
        if _k in _want:
            _lines[_i] = _want[_k]
            _seen.add(_k)
    for _k, _v in _want.items():
        if _k not in _seen:
            _lines.append(_v)
    try:
        Path(_p).write_text("\n".join(_lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def mode_flags(cp, mode, dmr_ready=False):
    # 所有数字模式先关闭，随后只打开本通道分配的模式。
    for sec,key in (("DMR","[DMR]"),("D-Star","[D-Star]"),("System Fusion","[System Fusion]"),
                    ("P25","[P25]"),("NXDN","[NXDN]"),("FM","[FM]"),("POCSAG","[POCSAG]")):
        setv(cp,sec,"Enable","0")
    for sec in ("DMR Network","D-Star Network","System Fusion Network","P25 Network",
                "NXDN Network","FM Network","POCSAG Network"):
        setv(cp,sec,"Enable","0")
    m=mode.upper()
    if m=="DMR":
        # [FIX 1.0.130] MMDVM-Host 在 Id 无效（全 0）时会断言崩溃：
        #   DMRNetwork.cpp  assert(id > 1000U)
        #   DMRControl.cpp  assert(id != 0U)
        # 所以只有拿到有效 DMR ID 时才同时打开 [DMR] 与 [DMR Network]；
        # 否则两者都关，MMDVM-Host 安静空转（不再断言重启）。
        # 用户在 Web 填好呼号/DMR ID 后，channel-setup 会重新把它们打开。
        if dmr_ready:
            setv(cp,"DMR","Enable","1")
            setv(cp,"DMR Network","Enable","1")
        else:
            setv(cp,"DMR","Enable","0")
            setv(cp,"DMR Network","Enable","0")
    elif m=="D-STAR":
        setv(cp,"D-Star","Enable","1"); setv(cp,"D-Star Network","Enable","1")
        # 与 ircddbgateway 的 repeaterBand1 保持一致
        setv(cp,"D-Star","Module",_dstar_module())
    elif m=="YSF":
        setv(cp,"System Fusion","Enable","1"); setv(cp,"System Fusion Network","Enable","1")
        _ysf_keepalive()
    elif m=="P25":
        setv(cp,"P25","Enable","1"); setv(cp,"P25 Network","Enable","1")
    elif m=="NXDN":
        setv(cp,"NXDN","Enable","1"); setv(cp,"NXDN Network","Enable","1")
        # 与 NXDNGateway 的 RptProtocol 保持一致（不一致会导致 NXDN 能听不能说）
        setv(cp,"NXDN Network","Protocol",_nxdn_protocol())
    elif m=="FM":
        setv(cp,"FM","Enable","1"); setv(cp,"FM Network","Enable","1")
        setv(cp,"FM Network","GatewayAddress","127.0.0.1"); setv(cp,"FM Network","GatewayPort","49880")
        # 9/13 好用参数：ModeHang=3（网络流保持，防断话）、LocalAddress 127.0.0.1
        setv(cp,"FM Network","ModeHang","3")
        setv(cp,"FM Network","LocalAddress","127.0.0.1")
        # CTCSS 必须生效（9/13 的 AccessMode=0 是缺陷）：接收常开 + 按开关决定发射
        _ct = {}
        try:
            _fmo = json.load(open("/opt/fmo-bridge/config.json", encoding="utf-8"))
            _ct = ((_fmo.get("fm") or {}).get("ctcss") or {})
        except Exception:
            _ct = {}
        try:
            _tone = float(_ct.get("freq") or _ct.get("rx_tone") or 88.5)
        except Exception:
            _tone = 88.5
        if not (60.0 <= _tone <= 260.0):
            _tone = 88.5
        setv(cp,"FM","CTCSSFrequency",("%.1f" % _tone).rstrip("0").rstrip("."))
        setv(cp,"FM","CTCSSHighThreshold","30")
        setv(cp,"FM","CTCSSLowThreshold","20")
        setv(cp,"FM","CTCSSLevel","2.0")
        setv(cp,"FM","AccessMode","1")          # 1 = 用 CTCSS 作为接入条件（接收哑音常开）
        setv(cp,"FM","LinkMode","1")
        setv(cp,"FM","NoiseSquelch","0")
        setv(cp,"FM","SquelchHighThreshold","140")
        setv(cp,"FM","SquelchLowThreshold","120")
        setv(cp,"General","RFModeHang","10")
        setv(cp,"General","NetModeHang","3")
        try:
            Path("/etc/mmdvm/fm-ctcss-tx.enable").write_text("1\n" if _ct.get("tx_enabled", False) else "0\n", encoding="ascii")
            os.chmod("/etc/mmdvm/fm-ctcss-tx.enable", 0o666)
        except Exception:
            pass
        # 注意：不再强制 ExtAudioBoost / FMTXLevel / HangTime / COSInvert / Ack* 等音频与时序参数，
        #       它们由模板与用户设置决定（沿用 9/13 的好用值）。

    elif m=="POCSAG":
        setv(cp,"POCSAG","Enable","1"); setv(cp,"POCSAG Network","Enable","1")


def _write_channel_bins(plan):
    os.makedirs(os.path.dirname(CHANNEL_BINS), exist_ok=True)
    rx = ",".join(str(int(x)) for x in plan.get("rx_bins", []))
    tx = ",".join(str(int(x)) for x in plan.get("tx_bins", []))
    with open(CHANNEL_BINS, "w", encoding="utf-8") as f:
        f.write("RX=" + rx + "\n")
        f.write("TX=" + tx + "\n")


def apply(plan):
    d=normalized(plan)
    _write_channel_bins(d)
    try:
        _saved_profiles, _saved_general = capture_user_settings()
    except Exception:
        _saved_profiles, _saved_general = {}, {}
    # 互斥规则只检查正在运行的通道；未启用通道保留的历史模式不算占用。
    _active_modes=[str(x.get("mode","NONE")).upper()
                   for x in d["list"][:int(d.get("channels") or 1)]
                   if str(x.get("mode","NONE")).upper() not in ("DMR","NONE","")]
    _dupes=sorted({m for m in _active_modes if _active_modes.count(m)>1})
    if _dupes:
        raise RuntimeError("模式互斥规则：运行中的非 DMR 模式只能各用一次。重复: %s" % ", ".join(_dupes))
    # 先复制模板，避免逐条生成时互相覆盖。
    if not os.path.exists(HOST_BASE):
        raise RuntimeError("缺少 /etc/MMDVM-Host.ini 模板")
    host_tpl=ini(HOST_BASE)
    iq_tpl=ini(IQ_BASE)
    call=resolve_callsign(d.get("callsign"))
    dmr_id=resolve_dmr_id(d.get("dmr_id"))
    for item in d["list"]:
        n=int(item["n"])
        if n>d["channels"]:
            continue
        h=copy.deepcopy(host_tpl)
        # [1.0.164] per-channel DMR identity: never re-apply channel 1 ID to channel 2/3/4.
        item_call=resolve_callsign(item.get("callsign")) or call
        item_base=str(item.get("dmr_id") or "").strip()
        item_essid=str(item.get("essid") or "").strip()
        if item_base:
            if len(item_base)==9 and not item_essid:
                item_essid=item_base[7:]
                item_base=item_base[:7]
            item_full_id=(item_base+item_essid) if item_essid else item_base
        else:
            item_full_id=dmr_id
        item_dmr_ready=_dmr_id_valid(item_full_id)
        item_cc=str(item.get("cc") or "1").strip()
        setv(h,"General","Callsign",item_call or "NOCALL")
        if item_full_id: setv(h,"General","Id",item_full_id)
        setv(h,"General","RXFrequency",item["rx"]); setv(h,"General","TXFrequency",item["tx"])
        setv(h,"Info","RXFrequency",item["rx"]); setv(h,"Info","TXFrequency",item["tx"])
        setv(h,"Modem","RXFrequency",item["rx"]); setv(h,"Modem","TXFrequency",item["tx"])
        setv(h,"Modem","ModemPort",3334+2*(n-1)); setv(h,"Modem","LocalPort",3335+2*(n-1))
        if str(item.get("mode","")).upper()=="DMR":
            setv(h,"DMR Network","GatewayPort",62031+(n-1)*10)
            setv(h,"DMR Network","LocalPort",62032+(n-1)*10)
            setv(h,"DMR","ColorCode",item_cc)
        mode_flags(h,item["mode"],item_dmr_ready)
        # Preserve user-editable settings across channel/mode regeneration.
        for _sec, _vals in (_saved_profiles.get(item["mode"], {}) or {}).items():
            for _k, _v in _vals.items():
                if _k.upper() not in SYSTEM_KEYS:
                    setv(h, _sec, _k, _v)
        for _k, _v in (_saved_general or {}).items():
            if _k.upper() not in SYSTEM_KEYS:
                setv(h, "General", _k, _v)
        setv(h,"DMR","TXHang","4"); setv(h,"DMR","CallHang","3")
        # [FIX 1.0.132] unique MQTT name/topic per channel
        setv(h,"MQTT","Name","mmdvmhost-%d"%n)
        save(h,"/etc/MMDVM-Host-%d.ini"%n)
        # 兼容旧页面/脚本读取的基础 Host 文件，必须与 1 号通道保持一致。
        if n == 1:
            save(h,HOST_BASE)
        q=copy.deepcopy(iq_tpl)
        setv(q,"Modem","Driver","Multi")
        setv(q,"Host","HostAddress","127.0.0.1"); setv(q,"Host","HostPort",3335+2*(n-1))
        setv(q,"Host","LocalAddress","127.0.0.1"); setv(q,"Host","LocalPort",3334+2*(n-1))
        setv(q,"Multi","ModemAddress","127.0.0.1"); setv(q,"Multi","ModemPort",48200+(n-1))
        setv(q,"Multi","LocalAddress","127.0.0.1"); setv(q,"Multi","LocalPort",48100+(n-1))
        setv(q,"MQTT","Name","mmdvm-iq-%d"%n)
        save(q,"/etc/MMDVM-IQ-%d.ini"%n)
    m=ini(MULTI)
    setv(m,"General","Channels",d["channels"]); setv(m,"Modem","SampleRate",d["sample_rate"])
    setv(m,"Modem","RxFrequency",d["center_rx"]); setv(m,"Modem","TxFrequency",d["center_tx"])
    save(m,MULTI)
    os.makedirs(os.path.dirname(PLAN),exist_ok=True)
    with open(PLAN,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False,indent=2)
    subprocess.run(["systemctl","daemon-reload"],timeout=15)
    limit=hardware_limit()
    for n in range(d["channels"]+1,12):
        subprocess.run(["systemctl","stop","mmdvm-iq@%d.service"%n,"mmdvmhost@%d.service"%n],timeout=25,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(["systemctl","disable","mmdvm-iq@%d.service"%n,"mmdvmhost@%d.service"%n],timeout=20,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for n in range(1,d["channels"]+1):
        subprocess.run(["systemctl","enable","mmdvm-iq@%d.service"%n,"mmdvmhost@%d.service"%n],timeout=20,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(["systemctl","restart","mmdvm-iq@%d.service"%n],timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(["systemctl","restart","mmdvmhost@%d.service"%n],timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(["systemctl","restart","mmdvm-multi.service"],timeout=40,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        propagate_gateway_identity(call, dmr_id)
    except Exception:
        pass
    sync_dmr_gateway(d)
    sync_gateway_services(d)
    print("多信道配置已应用：%d 路" % d["channels"])


def _sysq(args, timeout=60):
    # [FIX 1.0.101] 单个 systemctl 超时/失败不得中断整个 apply。
    # 实测本机 nxdngateway 重启会超过 40 秒，原实现抛超时 -> apply 中断 ->
    # 单元被留在半配置状态（有的 enable 有的 disable），重启后只剩部分通道。
    try:
        subprocess.run(args, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def sync_gateway_services(plan):
    modes = {str(x.get("mode", "")).upper().replace("-", "") for x in plan.get("list", [])[:int(plan.get("channels") or 0)]}
    pairs = (("DMR", "dmrgateway"), ("YSF", "ysfgateway"), ("P25", "p25gateway"), ("NXDN", "nxdngateway"), ("DSTAR", "ircddbgatewayd"), ("FM", "fmo-bridge"))
    # 先统一 enable/disable（快），再统一 restart（慢）；任何一步失败都不中断
    for mode, svc in pairs:
        if mode in modes:
            _sysq(["systemctl", "enable", svc], 30)
        else:
            _sysq(["systemctl", "disable", "--now", svc], 40)
    for mode, svc in pairs:
        if mode in modes:
            _sysq(["systemctl", "restart", svc], 150)


def set_mode(n,mode):
    limit=hardware_limit()
    if int(n)>limit:
        raise RuntimeError("当前硬件最多支持 %d 路，不能启用第 %d 路" % (limit,int(n)))
    d=normalized(None); mode=mode.upper()
    if mode not in MODES+("NONE",): raise RuntimeError("无效模式: %s"%mode)
    for x in d["list"]:
        if int(x["n"])==int(n): x["mode"]=mode
    d["channels"]=max(int(d.get("channels") or 1),int(n))
    apply(d)


def main():
    try:
        if len(sys.argv)>=4 and sys.argv[1]=="set":
            set_mode(int(sys.argv[2]),sys.argv[3]); return 0
        if len(sys.argv)>=2 and sys.argv[1]=="apply":
            plan=json.loads(sys.argv[2]) if len(sys.argv)>2 and sys.argv[2].strip() else normalized(None)
            apply(plan); return 0
        if len(sys.argv)>=2 and sys.argv[1]=="plan":
            print(json.dumps(normalized(None),ensure_ascii=False,indent=2)); return 0
        raise RuntimeError("用法: channel-setup.py {apply [json]|set n mode|plan}")
    except Exception as e:
        print(str(e),file=sys.stderr); return 1

if __name__=="__main__": raise SystemExit(main())

