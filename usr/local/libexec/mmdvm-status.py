#!/usr/bin/env python3
"""MMDVM-IQ 状态输出（QSO 从 journald 和日志文件双读）"""
import subprocess, os, re, json, glob, sys

_FAST = len(sys.argv) > 1 and sys.argv[1] == "quick"


def _active_channel_count():
    """当前实际启动的通道数；历史 Host-N.ini 不代表正在运行。"""
    try:
        with open("/etc/mmdvm/channels.json", encoding="utf-8") as f:
            n = int((json.load(f) or {}).get("channels") or 0)
        if n > 0:
            return max(1, min(11, n))
    except Exception:
        pass
    try:
        txt = open("/etc/MMDVM-Multi.ini", encoding="utf-8", errors="ignore").read()
        m = re.search(r"(?mi)^\s*Channels\s*=\s*(\d+)", txt)
        if m:
            return max(1, min(11, int(m.group(1))))
    except Exception:
        pass
    return 1


def _compute_modes():
    """动态统计当前运行通道模式（只扫描前 N 路 Host 配置）。"""
    secs = [("DMR", "DMR"), ("D-STAR", "D-Star"), ("YSF", "System Fusion"),
            ("P25", "P25"), ("NXDN", "NXDN"), ("FM", "FM"), ("POCSAG", "POCSAG")]
    modes = []
    n = _active_channel_count()
    for i in range(1, n + 1):
        try:
            t = open("/etc/MMDVM-Host-%d.ini" % i, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        cur = None
        for ln in t.split("\n"):
            ln = ln.strip()
            if ln.startswith("["):
                cur = ln.strip("[]")
            elif ln == "Enable=1" and cur:
                for short, sec in secs:
                    if cur == sec and short not in modes:
                        modes.append(short)
    return ("MODE=" + "+".join(modes) + "\nCHANNELS=%d" % n) if modes else "MODE=NONE"



QSO_GREP = r'DMR Slot [12].*received (rf|network) (voice header|end of voice)|received (network|rf) (voice header|end of voice)|received (network|rf) end of transmission|received (network|rf) (transmission|data from)|d-star, received|FM received|网络流结束|network watchdog has expired'


def get_qso():
    """从 journald 和日志文件收集 QSO。
    ★ 性能：一次 journalctl 批量拉取全部相关单元（json 输出），
    不再逐通道各跑一次（旧版 8 次调用，每次 0.5~1.5 秒）。"""
    all_lines = []
    _n = _active_channel_count()
    units = ["mmdvmhost.service"] + ["mmdvmhost@%d.service" % i for i in range(1, _n + 1)]
    try:
        if subprocess.run(["systemctl", "is-active", "--quiet", "fmo-bridge.service"], capture_output=True, timeout=3).returncode == 0:
            units.append("fmo-bridge.service")
    except Exception:
        pass
    try:
        cmd = ["journalctl"]
        for unit in units:
            cmd += ["-u", unit]
        cmd += ["--since", "5 minutes ago", "-n", "1000", "-g", QSO_GREP, "--case-sensitive=no", "--no-pager", "-o", "json"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=18)
        if r.returncode == 0:
            for raw in r.stdout.splitlines():
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                msg = str(obj.get("MESSAGE") or "")
                unit = str(obj.get("_SYSTEMD_UNIT") or "")
                m = re.search(r"mmdvmhost@(\d+)\.service$", unit)
                if m:
                    msg = "[CH%s] %s" % (m.group(1), msg)
                if msg:
                    if "fmo-bridge" in unit and "[CH" not in msg:
                        msg="[CH6] "+msg
                    all_lines.append(msg)
    except Exception:
        pass

    # 日志文件备用（journald 不可用时才读）
    if not all_lines:
        files = []
        for pat in ("/var/log/mmdvm/MMDVM-Host-*.log", "/var/log/mmdvm/MMDVM-Host*.log"):
            files.extend(glob.glob(pat))
        for f in sorted(set(files), reverse=True)[:2]:
            try:
                with open(f, encoding="utf-8", errors="ignore") as fh:
                    all_lines.extend(fh.readlines()[-200:])
            except Exception:
                pass

    KWS = (
        "received rf end of voice",
        "received network end of voice",
        "received rf voice header",
        "received network voice header",
        # ★ YSF/P25/NXDN 网络语音没有 header 行，只有“开始行”
        "received network data from",
        "received network transmission",
        "received rf voice transmission",
        "network watchdog has expired",
        "d-star, received rf header",
        "d-star, received network header",
        "d-star, received rf end of transmission",
        "d-star, received network end of transmission",
        "d-star, received rf end of voice",
        "d-star, received network end of voice",
        "received rf header",
        "received network header",
        "end of transmission",
    )
    qso, seen = [], set()
    for line in all_lines:
        low = line.lower()
        if any(kw in low for kw in KWS):
            key = re.sub(r"\s+", " ", line.strip())[:160]
            if key not in seen:
                seen.add(key)
                qso.append(line.rstrip())

    def _ts_of(x):
        m = re.search(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})", x)
        return m.group(1) if m else ""

    qso.sort(key=_ts_of, reverse=True)
    return qso[:80]

# ==================== ★ 快速模式：只输出通话记录（供 Live QSO 1 秒轮询，跳过服务/网络/磁盘）====================
if len(sys.argv) > 1 and sys.argv[1] == "qso":
    print("=== QSO ===")
    _q = get_qso()
    if _q:
        for _l in _q:
            print(_l)
    else:
        print("无通话记录")
    sys.exit(0)

# === SERVICES ===
def _any_active(units):
    """多信道架构下 mmdvmhost/mmdvm-iq 是实例化单元，需逐个探测"""
    for u in units:
        try:
            r = subprocess.run(["systemctl", "is-active", "--quiet", u],
                               capture_output=True, timeout=3)
            if r.returncode == 0:
                return True
        except Exception:
            pass
    return False


_UNITS = [
    ("mmdvm-iq",  ["mmdvm-iq@%d.service" % i for i in (1, 2, 3, 4, 5, 6)] + ["mmdvm-iq.service"]),
    ("mmdvmhost", ["mmdvmhost@%d.service" % i for i in (1, 2, 3, 4, 5, 6)] + ["mmdvmhost.service"]),
    ("mmdvm-multi", ["mmdvm-multi.service"]),
    ("dmrgateway", ["dmrgateway.service"]),
    ("ysfgateway", ["ysfgateway.service"]),
    ("p25gateway", ["p25gateway.service"]),
    ("nxdngateway", ["nxdngateway.service"]),
    ("ircddbgatewayd", ["ircddbgatewayd.service"]),
    ("mosquitto", ["mosquitto.service"]),
    ("mmdvm-web", ["mmdvm-web.service"]),
]
print("=== SERVICES ===")
_all_units = sorted({u for _name, _units in _UNITS for u in _units})
_active_set = set()
try:
    _r = subprocess.run(["systemctl", "is-active"] + _all_units,
                        capture_output=True, text=True, timeout=12)
    _active_set = {u for u, state in zip(_all_units, _r.stdout.splitlines())
                   if state.strip() == "active"}
except Exception:
    pass
for _name, _units in _UNITS:
    print("%s %s" % (_name, "active" if any(u in _active_set for u in _units) else "inactive"))

# === PROFILE ===
print("=== PROFILE ===")
try:
    content = _compute_modes()
    print(content if content else "MODE=未知")
except Exception:
    print("MODE=未知")

# === SYSTEM ===
print("=== SYSTEM ===")

try:
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        cpu = int(f.read().strip()) / 1000.0
    print(f"cpu_temp={cpu:.1f}")
except Exception:
    print("cpu_temp=N/A")

try:
    d = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            d[k] = int(v.strip().split()[0])
    total_kb = d.get("MemTotal", 0)
    avail_kb = d.get("MemAvailable", 0)
    used_kb = total_kb - avail_kb
    print(f"mem={used_kb//1024}M/{total_kb//1024}M")
except Exception:
    print("mem=N/A")

try:
    with open("/proc/uptime") as f:
        secs = float(f.read().split()[0])
    days = int(secs // 86400); hours = int((secs % 86400) // 3600)
    mins = int((secs % 3600) // 60)
    if days > 0: print(f"uptime=up {days} days, {hours} hours")
    elif hours > 0: print(f"uptime=up {hours} hours, {mins} mins")
    else: print(f"uptime=up {mins} mins")
except Exception:
    print("uptime=N/A")

try:
    with open("/proc/loadavg") as f:
        parts = f.read().split()
    print(f"load={parts[0]}, {parts[1]}, {parts[2]}")
except Exception:
    print("load=N/A")

# ★ CPU 实时占用率：保存最近一次 /proc/stat 样本。quick 状态只读缓存并
# 用差值计算，不再每个请求都阻塞 0.25 秒。
try:
    def _read_stat():
        with open("/proc/stat") as f:
            fld = f.readline().split()
        vals = [int(x) for x in fld[1:8]]
        total = sum(vals)
        idle = vals[3] + vals[4]
        return total, idle
    import time as _time
    _cpu_cache = "/run/arch-cpu-usage.json"
    _t1, _i1 = _read_stat()
    _now = _time.time()
    _pct = None
    try:
        _c = json.load(open(_cpu_cache, encoding="utf-8"))
        _dt = _t1 - int(_c.get("total", _t1))
        _di = _i1 - int(_c.get("idle", _i1))
        if _now - float(_c.get("time", 0)) >= 0.2 and _dt > 0 and 0 <= _di <= _dt:
            _pct = 100.0 * (_dt - _di) / _dt
    except Exception:
        pass
    if _pct is None and not _FAST:
        _time.sleep(0.25)
        _t2, _i2 = _read_stat()
        _dt = _t2 - _t1
        _di = _i2 - _i1
        if _dt > 0 and 0 <= _di <= _dt:
            _pct = 100.0 * (_dt - _di) / _dt
        _t1, _i1 = _t2, _i2
        _now = _time.time()
    if _pct is None:
        print("cpu_usage=N/A")
    else:
        print(f"cpu_usage={_pct:.0f}%")
    try:
        with open(_cpu_cache, "w", encoding="utf-8") as _f:
            json.dump({"total": _t1, "idle": _i1, "time": _now}, _f)
    except Exception:
        pass
except Exception:
    print("cpu_usage=N/A")

try:
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize; free = st.f_bavail * st.f_frsize
    used = total - free; pct = (used * 100 // total) if total else 0
    print(f"disk={used//(1024**3)}G/{total//(1024**3)}G ({pct}%)")
except Exception:
    print("disk=N/A")

print("sx_probe=OK" if os.path.exists("/etc/mmdvm/soapy-sx-verified")
      else "sx_probe=FAIL")

# === NETWORK ===
print("=== NETWORK ===")
def _default_gateways():
    result = {}
    try:
        r = subprocess.run(["ip","route","show"], capture_output=True, text=True, timeout=3)
        for line in r.stdout.split("\n"):
            parts = line.split()
            if line.startswith("default") and "via" in parts and "dev" in parts:
                result[parts[parts.index("dev") + 1]] = parts[parts.index("via") + 1]
    except Exception:
        pass
    return result

def _iface_status(name):
    ipv4 = ""
    state = "disconnected"
    try:
        r = subprocess.run(["ip","-br","addr","show","dev",name], capture_output=True, text=True, timeout=3)
        line = (r.stdout or "").strip().split("\n")[0]
        parts = line.split()
        if len(parts) >= 3:
            state = "connected" if ("UP" in parts[1].upper()) and len(parts) >= 3 else "disconnected"
            for item in parts[2:]:
                addr = item.split("/")[0]
                if "." in addr and not addr.startswith("127."):
                    ipv4 = addr
                    break
    except Exception:
        pass
    if not ipv4:
        state = "disconnected"
    return state, ipv4

try:
    r = subprocess.run(["hostname","-I"], capture_output=True, text=True, timeout=3)
    ips = r.stdout.strip().split()
    print(f"ip={ips[0] if ips else '127.0.0.1'}")
except Exception:
    print("ip=127.0.0.1")

_gws = _default_gateways()
for _name, _key in (("eth0", "ETH0"), ("wlan0", "WLAN0")):
    _st, _ip4 = _iface_status(_name)
    print(f"{_key}_STATE={_st}")
    print(f"{_key}_IP={_ip4 or '-'}")
    print(f"{_key}_GW={_gws.get(_name, '-')}")

# First non-standard Ethernet/USB/WWAN interface is exposed as USBETH.
_extra = []
try:
    r = subprocess.run(["ip","-br","link","show"], capture_output=True, text=True, timeout=3)
    for line in r.stdout.split("\n"):
        parts = line.split()
        if not parts:
            continue
        name = parts[0].rstrip("@")
        if name in ("lo", "eth0", "wlan0"):
            continue
        if name.startswith(("eth", "enx", "usb", "wwan", "cdc")):
            _extra.append(name)
except Exception:
    pass
if _extra:
    _name = _extra[0]
    _st, _ip4 = _iface_status(_name)
    print(f"USBETH_NAME={_name}")
    print(f"USBETH_STATE={_st}")
    print(f"USBETH_IP={_ip4 or '-'}")
    print(f"USBETH_GW={_gws.get(_name, '-')}")

try:
    r = subprocess.run(["ip","route","show"], capture_output=True, text=True, timeout=3)
    gw = ""
    for line in r.stdout.split("\n"):
        if line.startswith("default"):
            parts = line.split()
            if "via" in parts:
                gw = parts[parts.index("via")+1]; break
    print(f"gateway={gw or 'N/A'}")
except Exception:
    print("gateway=N/A")

try:
    dns = ""
    with open("/etc/resolv.conf") as f:
        for line in f:
            if line.startswith("nameserver"):
                dns = line.split()[1]; break
    print(f"dns={dns or 'N/A'}")
except Exception:
    print("dns=N/A")

# === QSO ===
print("=== QSO ===")
if _FAST:
    print("无通话记录")
else:
    qso = get_qso()
    if qso:
        for line in qso:
            print(line)
    else:
        print("无通话记录")


