#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ARCH 热点参数保存工具。"""
import configparser
import glob
import json
import os
import re
import subprocess
import shutil
import sys

HOST = "/etc/MMDVM-Host.ini"
DMRGW = "/etc/DMRGateway.ini"
PLAN = "/etc/mmdvm/channels.json"
DMR_READY = "/etc/mmdvm/dmr-ready"
YSFGW = "/etc/YSFGateway.ini"
P25GW = "/etc/P25Gateway.ini"
NXDNGW = "/etc/NXDNGateway.ini"
IRCDDB = "/etc/ircddbgateway"
LIMIT_SCRIPT = "/usr/local/libexec/arch-channel-limit.py"


def run(cmd, timeout=40):
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=timeout)
    except Exception as e:
        return type("R", (), {"returncode": -1, "stdout": "", "stderr": str(e)})()


def ini(path):
    cp = configparser.RawConfigParser()
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
    except Exception:
        pass
    return cp


def save_ini(cp, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for sec in cp.sections():
            f.write("[" + sec + "]\n")
            for k, v in cp.items(sec, raw=True):
                f.write(str(k) + "=" + str(v) + "\n")
    os.replace(tmp, path)


def set_sec(cp, sec, key, val):
    if not cp.has_section(sec):
        cp.add_section(sec)
    cp.set(sec, key, str(val))

def _gateway_span(lines, section):
    header = '[' + section.lower() + ']'
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == header:
            start = i + 1
            break
    if start is None:
        lines.extend(['\n[' + section + ']\n'])
        start = len(lines)
    end = start
    while end < len(lines):
        s = lines[end].strip()
        if s.startswith('[') and s.endswith(']'):
            break
        end += 1
    return start, end


def _gateway_set(lines, section, key, value):
    start, end = _gateway_span(lines, section)
    target = key.lower() + '='
    for i in range(start, end):
        if lines[i].strip().lower().startswith(target):
            lines[i] = key + '=' + str(value) + '\n'
            return
    lines.insert(end, key + '=' + str(value) + '\n')


def _gateway_strip(lines, section, prefixes):
    start, end = _gateway_span(lines, section)
    i = start
    while i < end:
        key = lines[i].split('=', 1)[0].strip().lower() if '=' in lines[i] else ''
        if any(key.startswith(x.lower()) for x in prefixes):
            del lines[i]
            end -= 1
        else:
            i += 1


def save_dmr_gateway(path, values):
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    for sec, kv in values.items():
        for key, val in kv.items():
            _gateway_set(lines, sec, key, val)
    _gateway_strip(lines, 'DMR Network 1', ('PassAllTG','PassAllPC','TGRewrite','PCRewrite','TypeRewrite','SrcRewrite','TGDynRewrite'))
    start, end = _gateway_span(lines, 'DMR Network 1')
    rules = ['PassAllTG=1\n','PassAllTG=2\n','PassAllPC=1\n','PassAllPC=2\n',
             'TGRewrite=1,9,1,9,1\n','TGRewrite=2,9,2,9,1\n']
    lines[end:end] = rules
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    os.replace(tmp, path)


def set_flat(path, values):
    lines = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        pass
    seen = set()
    out = []
    for line in lines:
        s = line.strip()
        if "=" in s and not s.startswith(("#", ";", "[")):
            k = s.split("=", 1)[0].strip()
            if k in values:
                out.append("%s=%s\n" % (k, values[k]))
                seen.add(k)
                continue
        out.append(line if line.endswith("\n") else line + "\n")
    for k, v in values.items():
        if k not in seen:
            out.append("%s=%s\n" % (k, v))
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)


def clean_call(v):
    return re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper()[:16]


def clean_id(v):
    s = re.sub(r"\D", "", str(v or ""))
    return s[:9]


def clean_essid(v):
    s = re.sub(r"\D", "", str(v or ""))
    if not s:
        return ""
    try:
        n = int(s)
    except Exception:
        raise ValueError("ESSID 必须是 01-99 之间的两位数字")
    if n < 1 or n > 99:
        raise ValueError("ESSID 必须是 01-99 之间的两位数字")
    return "%02d" % n


def freq(v, old="0"):
    try:
        n = int(float(str(v).strip()))
        return str(n) if n > 0 else str(old)
    except Exception:
        return str(old)


def host_paths():
    paths = [HOST]
    paths += sorted(glob.glob("/etc/MMDVM-Host-*.ini"))
    return list(dict.fromkeys(paths))


def set_identity(callsign, dmr_id="", freqs=None):
    for p in host_paths():
        cp = ini(p)
        set_sec(cp, "General", "Callsign", callsign)
        if dmr_id:
            set_sec(cp, "General", "Id", dmr_id)
        if freqs:
            rx, tx = freqs
            set_sec(cp, "General", "RXFrequency", rx)
            set_sec(cp, "General", "TXFrequency", tx)
            set_sec(cp, "Info", "RXFrequency", rx)
            set_sec(cp, "Info", "TXFrequency", tx)
            set_sec(cp, "Modem", "RXFrequency", rx)
            set_sec(cp, "Modem", "TXFrequency", tx)
        save_ini(cp, p)


def active_channels():
    try:
        r = run([LIMIT_SCRIPT], 8)
        limit = max(1, min(7, int((r.stdout or "7").strip())))
    except Exception:
        limit = 7
    try:
        d = json.load(open(PLAN, encoding="utf-8"))
        return [int(x.get("n", 1)) for x in d.get("list", []) if x.get("mode") not in (None, "", "NONE")][:limit]
    except Exception:
        return [1]


def load_plan():
    try:
        with open(PLAN, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def plan_by_channel(plan=None):
    plan = plan or load_plan()
    result = {}
    for item in plan.get("list", []):
        try:
            result[int(item.get("n"))] = item
        except Exception:
            pass
    return result


def write_plan(plan):
    os.makedirs(os.path.dirname(PLAN), exist_ok=True)
    tmp = PLAN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, PLAN)


def update_plan_identity(callsign="", dmr_id=""):
    plan = load_plan()
    if not plan:
        return
    if callsign:
        plan["callsign"] = callsign
    if dmr_id:
        plan["dmr_id"] = dmr_id
    write_plan(plan)


def host_channel_number(path):
    m = re.search(r"/etc/MMDVM-Host-(\d+)\.ini$", path)
    if m:
        return int(m.group(1))
    return 1


def sync_host_identity_and_frequencies(callsign="", dmr_id=""):
    """同步频点；DMR 通道优先使用 channels.json 中绑定的独立身份。"""
    by_channel = plan_by_channel()
    for path in host_paths():
        cp = ini(path)
        n = host_channel_number(path)
        item = by_channel.get(n) or {}
        ch_call = clean_call(item.get("callsign") or "") or callsign
        ch_id = dmr_id
        item_base = clean_id(item.get("dmr_id") or "")
        if item_base:
            try:
                item_essid = clean_essid(item.get("essid")) if item.get("essid") else ""
            except Exception:
                item_essid = ""
            if len(item_base) == 9 and not item_essid:
                item_essid = item_base[7:]
                item_base = item_base[:7]
            ch_id = item_base + item_essid if item_essid else item_base
        if ch_call:
            set_sec(cp, "General", "Callsign", ch_call)
        if ch_id:
            set_sec(cp, "General", "Id", ch_id)
        if item:
            rx, tx = item.get("rx"), item.get("tx")
            if rx:
                set_sec(cp, "General", "RXFrequency", rx)
                set_sec(cp, "Info", "RXFrequency", rx)
                set_sec(cp, "Modem", "RXFrequency", rx)
            if tx:
                set_sec(cp, "General", "TXFrequency", tx)
                set_sec(cp, "Info", "TXFrequency", tx)
                set_sec(cp, "Modem", "TXFrequency", tx)
        save_ini(cp, path)


def restart_channels(mode=None):
    for n in active_channels():
        if mode is None:
            run(["systemctl", "restart", "mmdvm-iq@%d.service" % n], 30)
        run(["systemctl", "restart", "mmdvmhost@%d.service" % n], 30)
    run(["systemctl", "restart", "mmdvm-multi.service"], 40)


def restart_gateway_if_active(name):
    try:
        r = run(["systemctl", "is-active", name + ".service"], 5)
        if (r.stdout or "").strip() == "active":
            run(["systemctl", "restart", name + ".service"], 40)
    except Exception:
        pass


def propagate_global_callsign(call):
    """Global RF callsign -> every mode configuration."""
    call = clean_call(call)
    if not call:
        return
    try:
        cp = ini(DMRGW)
        set_sec(cp, "General", "Callsign", call)
        set_sec(cp, "Info", "Callsign", call)
        save_ini(cp, DMRGW)
    except Exception:
        pass
    for path in (YSFGW, P25GW, NXDNGW):
        try:
            cp = ini(path)
            set_sec(cp, "General", "Callsign", call)
            save_ini(cp, path)
        except Exception:
            pass
    try:
        set_flat(IRCDDB, {"gatewayCallsign": call, "repeaterCall1": call})
    except Exception:
        pass
    for svc in ("dmrgateway", "ysfgateway", "p25gateway", "nxdngateway", "ircddbgatewayd"):
        restart_gateway_if_active(svc)


def save_rf(a):
    call, dmr_id = clean_call(a[0]), clean_id(a[1])
    update_plan_identity(call, dmr_id)
    sync_host_identity_and_frequencies(call, dmr_id)
    if call:
        propagate_global_callsign(call)
    restart_channels()
    print("基础射频参数已保存；呼号已同步到各模式网关；实际频点以通道规划为准，未被改写")


def _dmr_gateway_ports(n):
    """每个 DMR 通道独立网关端口：通道1=62031/62032，通道2=62041/62042 ..."""
    n = max(1, int(n))
    return 62031 + (n - 1) * 10, 62032 + (n - 1) * 10


def save_dmr(a):
    call = clean_call(a[0])
    base_id = clean_id(a[1])
    cc = a[2] or "1"
    legacy_host = a[5] if len(a) > 5 else ""
    legacy_port = a[6] if len(a) > 6 and a[6] else "62031"
    legacy_pw = a[7] if len(a) > 7 else ""
    essid = clean_essid(a[8]) if len(a) > 8 else ""
    raw_channels = []
    if len(a) > 9 and a[9]:
        try:
            raw_channels = json.loads(a[9])
        except Exception:
            raw_channels = []
    if not call or call == "NOCALL":
        raise ValueError("呼号未填写")
    # [1.0.161] 全 0 ID 会让 DMRGateway 直接 assert(id > 0) 并进入重启循环。
    if not base_id or not re.search(r"[1-9]", base_id):
        raise ValueError("DMR ID 未填写或无效")
    if essid and len(base_id) != 7:
        raise ValueError("启用 ESSID 时，DMR ID 必须是 7 位数字")
    effective_id = base_id + essid if essid else base_id
    dmr_items = sorted(
        [x for x in plan_by_channel().values() if str(x.get("mode", "")).upper() == "DMR"],
        key=lambda x: int(x.get("n", 0)))
    if not dmr_items:
        raise ValueError("当前通道规划中没有 DMR 通道，请先在射频模式配置中设置")

    update_plan_identity(call, base_id)

    def channel_cfg(n):
        # [1.0.164] per-channel DMR identity
        path = DMRGW if n == 1 else ("/etc/DMRGateway-%d.ini" % n)
        if n > 1 and not os.path.exists(path):
            try:
                shutil.copy2(DMRGW, path)
            except Exception:
                pass
        cur_host = cur_port = cur_pw = cur_call = cur_full_id = cur_cc = ""
        try:
            _g = ini(path)
            cur_host = _g.get("DMR Network 1", "Address", fallback="").strip()
            cur_port = _g.get("DMR Network 1", "Port", fallback="").strip()
            cur_pw = _g.get("DMR Network 1", "Password", fallback="")
            cur_call = _g.get("General", "Callsign", fallback="").strip()
            cur_full_id = _g.get("General", "Id", fallback="").strip()
            cur_cc = _g.get("Info", "ColorCode", fallback="").strip()
        except Exception:
            pass
        item = {}
        for x in raw_channels:
            try:
                if int(x.get("n") or 0) == n:
                    item = x if isinstance(x, dict) else {}
                    break
            except Exception:
                pass
        host = str(item.get("host") or "").strip() if item else ""
        port = str(item.get("port") or "").strip() if item else ""
        pw = str(item.get("password") or "") if item else ""
        host = host or cur_host or (legacy_host if n == 1 else "")
        port = port or cur_port or (legacy_port if n == 1 else "62031")
        pw = pw or cur_pw or (legacy_pw if n == 1 else "")
        if not pw:
            pw = "x"
        if not host:
            raise ValueError("DMR 射频通道 %d 未填写服务器地址" % n)

        if n == 1:
            ch_call = call
            ch_base = base_id
            ch_essid = essid
            if len(ch_base) == 9 and not ch_essid:
                ch_essid = ch_base[7:]
                ch_base = ch_base[:7]
            ch_cc = str(cc or cur_cc or "1")
        else:
            ch_call = clean_call(item.get("callsign") if item else "") or clean_call(cur_call) or call
            raw_id = clean_id(item.get("id") if item else "") or clean_id(cur_full_id)
            raw_essid = clean_essid(item.get("essid")) if item and item.get("essid") else ""
            if len(raw_id) == 9:
                ch_base = raw_id[:7]
                ch_essid = raw_essid or raw_id[7:]
            elif raw_id:
                ch_base = raw_id
                ch_essid = raw_essid
            else:
                ch_base = base_id
                ch_essid = essid
            ch_cc = str(item.get("cc") if item and item.get("cc") not in (None, "") else (cur_cc or cc or "1"))
        ch_call = clean_call(ch_call)
        ch_base = clean_id(ch_base)
        ch_essid = clean_essid(ch_essid) if ch_essid else ""
        if not ch_call or ch_call == "NOCALL":
            raise ValueError("DMR 射频通道 %d 未填写呼号" % n)
        if not ch_base or not re.search(r"[1-9]", ch_base):
            raise ValueError("DMR 射频通道 %d 的 DMR ID 未填写或无效" % n)
        if len(ch_base) != 7:
            raise ValueError("DMR 射频通道 %d 的 DMR ID 必须是 7 位数字" % n)
        try:
            ccnum = int(str(ch_cc).strip())
            if not (0 <= ccnum <= 15):
                raise ValueError
            ch_cc = str(ccnum)
        except Exception:
            ch_cc = "1"
        ch_eff = ch_base + ch_essid if ch_essid else ch_base
        try:
            pnum = int(str(port).strip())
            if not (1 <= pnum <= 65535):
                raise ValueError
            port = str(pnum)
        except Exception:
            port = "62031"
        return {"n": n, "host": host, "port": port, "password": pw,
                "callsign": ch_call, "base_id": ch_base, "essid": ch_essid,
                "effective_id": ch_eff, "cc": ch_cc}

    dmr_numbers = set()
    saved_channels = {}
    for item in dmr_items:
        n = int(item.get("n"))
        dmr_numbers.add(n)
        ch = channel_cfg(n)
        saved_channels[n] = ch
        path = DMRGW if n == 1 else ("/etc/DMRGateway-%d.ini" % n)
        local_port, rpt_port = _dmr_gateway_ports(n)
        rx, tx = str(item.get("rx") or ""), str(item.get("tx") or "")
        values = {
            "General": {"Id": ch["effective_id"], "Callsign": ch["callsign"],
                         "LocalPort": str(local_port), "RptPort": str(rpt_port)},
            "Info": {"Callsign": ch["callsign"], "TXFrequency": tx,
                     "RXFrequency": rx, "ColorCode": ch["cc"]},
            "DMR Network 1": {"Enabled": "1", "Name": "BM", "Id": ch["effective_id"],
                              "Address": ch["host"], "Port": ch["port"],
                              "Password": ch["password"], "Location": "1", "Debug": "0"},
            "MQTT": {"Name": "dmr-gateway-%d" % n},
        }
        save_dmr_gateway(path, values)

        hp = "/etc/MMDVM-Host-%d.ini" % n
        cp = ini(hp)
        set_sec(cp, "General", "Callsign", ch["callsign"])
        set_sec(cp, "General", "Id", ch["effective_id"])
        for sec in ("General", "Info", "Modem"):
            set_sec(cp, sec, "RXFrequency", item.get("rx"))
            set_sec(cp, sec, "TXFrequency", item.get("tx"))
        set_sec(cp, "DMR", "Enable", "1")
        set_sec(cp, "DMR", "ColorCode", ch["cc"])
        set_sec(cp, "DMR Network", "Enable", "1")
        set_sec(cp, "DMR Network", "GatewayAddress", "127.0.0.1")
        set_sec(cp, "DMR Network", "GatewayPort", str(local_port))
        set_sec(cp, "DMR Network", "LocalAddress", "127.0.0.1")
        set_sec(cp, "DMR Network", "LocalPort", str(rpt_port))
        save_ini(cp, hp)

    # Persist each DMR RF channel identity in channels.json, so RF mode switching
    # cannot detach channel 2/3/4 from its own callsign/ID/server.
    try:
        _plan = load_plan()
        _by_n = {}
        for x in _plan.get("list", []):
            try:
                _by_n[int(x.get("n"))] = x
            except Exception:
                pass
        for n, ch in saved_channels.items():
            x = _by_n.get(n)
            if x is None:
                continue
            x["callsign"] = ch["callsign"]
            x["dmr_id"] = ch["base_id"]
            x["essid"] = ch["essid"]
            x["cc"] = int(ch["cc"])
        _plan["callsign"] = call
        _plan["dmr_id"] = base_id
        write_plan(_plan)
    except Exception:
        pass

    os.makedirs(os.path.dirname(DMR_READY), exist_ok=True)
    open(DMR_READY, "w").write(effective_id + "\n")
    for n in range(2, 8):
        if n not in dmr_numbers:
            run(["systemctl", "disable", "--now", "dmrgateway@%d.service" % n], 20)
    for n in sorted(dmr_numbers):
        unit = "dmrgateway.service" if n == 1 else ("dmrgateway@%d.service" % n)
        run(["systemctl", "enable", unit], 20)
        run(["systemctl", "restart", unit], 40)
    restart_channels()
    print("DMR 参数已保存（%d 个 DMR 通道；%s）" % (
        len(dmr_items),
        ", ".join("通道%d=%s" % (n, ch["effective_id"]) for n, ch in sorted(saved_channels.items()))))


def _restart_gateway(name):
    run(["systemctl", "restart", name + ".service"], 40)


def save_ysf(a):
    call = clean_call(a[0]); dmr_id = clean_id(a[1]) if len(a) > 1 else ""
    suffix = (a[2] if len(a) > 2 and a[2] else "RPT").upper()
    startup = a[3] if len(a) > 3 else ""
    set_identity(call, dmr_id)
    cp = ini(YSFGW)
    set_sec(cp, "General", "Callsign", call)
    set_sec(cp, "General", "Suffix", suffix)
    if dmr_id:
        set_sec(cp, "General", "Id", dmr_id)
    if startup:
        set_sec(cp, "Network", "Startup", startup)
    if len(a) > 4 and str(a[4]).strip() != "":
        set_sec(cp, "General", "WiresXCommandPassthrough", a[4])
    save_ini(cp, YSFGW)
    restart_channels()
    _restart_gateway("ysfgateway")
    print("YSF 参数已保存，YSFGateway 已重载")


def save_p25(a):
    call = clean_call(a[0] if a else "")
    _p25_nac = clean_id(a[1]) if len(a) > 1 else ""
    try:
        _p25_nac = str(int(_p25_nac)) if _p25_nac else ""
    except Exception:
        _p25_nac = ""
    if _p25_nac and not (0 < int(_p25_nac) <= 4095):
        _p25_nac = ""
    _p25_static = (a[2] if len(a) > 2 else "").strip() or "46002"
    set_identity(call)
    # NAC 必须写进 Host，否则手台 NAC 对不上就打不开（与 DMR ColorCode / NXDN RAN 同理）
    for n, item in plan_by_channel().items():
        if str(item.get("mode", "")).upper() != "P25":
            continue
        hp = "/etc/MMDVM-Host-%d.ini" % n
        h = ini(hp)
        set_sec(h, "P25", "Enable", "1")
        if _p25_nac:
            set_sec(h, "P25", "NAC", _p25_nac)
        set_sec(h, "P25 Network", "Enable", "1")
        save_ini(h, hp)
    cp = ini(P25GW)
    set_sec(cp, "General", "Callsign", call)
    # 静态通话组用页面选择的值（默认 46002），不再写死，用户才能选服务器
    set_sec(cp, "Network", "Static", _p25_static)
    save_ini(cp, P25GW)
    restart_channels()
    _restart_gateway("p25gateway")
    print("P25 参数已保存（NAC=%s Static=%s），P25Gateway 已重载" % (_p25_nac or "保持", _p25_static))


def save_nxdn(a):
    call = clean_call(a[0] if a else "")
    # [FIX 1.0.156] NXDNGateway ? Callsign ?????????????? NXDN ??/????
    # ?????????????????? NOCALL?????????
    if not call:
        try:
            _cur = ini(NXDNGW)
            call = clean_call(_cur.get("General", "Callsign", fallback=""))
        except Exception:
            call = ""
    if not call:
        call = "NOCALL"
    nxdn_id = clean_id(a[1]) if len(a) > 1 else ""
    _nxdn_ran = clean_id(a[2]) if len(a) > 2 else ""
    try:
        _nxdn_ran = str(int(_nxdn_ran)) if _nxdn_ran else ""
    except Exception:
        _nxdn_ran = ""
    if _nxdn_ran and not (0 <= int(_nxdn_ran) <= 63):
        _nxdn_ran = ""
    protocol = a[3] if len(a) > 3 and a[3] else "Icom"
    if str(protocol).strip().lower() not in ("icom", "kenwood"):
        protocol = "Icom"
    static = a[4] if len(a) > 4 else ""
    # 只更新 NXDN 页面所属的通道，避免覆盖 DMR / P25 的身份。
    for n, item in plan_by_channel().items():
        if str(item.get("mode", "")).upper() != "NXDN":
            continue
        hp = "/etc/MMDVM-Host-%d.ini" % n
        h = ini(hp)
        set_sec(h, "General", "Callsign", call)
        if nxdn_id:
            set_sec(h, "General", "Id", nxdn_id)
        # Host 的 NXDN Network Protocol 必须与网关 RptProtocol 相同
        set_sec(h, "NXDN Network", "Protocol", protocol)
        # RAN 必须写进 Host，否则手台 RAN 对不上就打不开
        set_sec(h, "NXDN", "Enable", "1")
        if _nxdn_ran:
            set_sec(h, "NXDN", "RAN", _nxdn_ran)
        set_sec(h, "NXDN Network", "Enable", "1")
        save_ini(h, hp)
    cp = ini(NXDNGW)
    set_sec(cp, "General", "Callsign", call)
    if nxdn_id:
        set_sec(cp, "General", "Id", nxdn_id)
    set_sec(cp, "General", "RptProtocol", protocol)
    # [FIX 1.0.87] [Info] 频率必须用通道规划的实际值（模板写死 430.475，与实机 434.475 不符）
    _nx_rx = _nx_tx = ""
    for _n, _it in plan_by_channel().items():
        if str(_it.get("mode", "")).upper() == "NXDN":
            _nx_rx = str(_it.get("rx") or "")
            _nx_tx = str(_it.get("tx") or "")
            break
    if _nx_rx:
        set_sec(cp, "Info", "RXFrequency", _nx_rx)
    if _nx_tx:
        set_sec(cp, "Info", "TXFrequency", _nx_tx)
    if static:
        set_sec(cp, "Network", "Static", static)
    save_ini(cp, NXDNGW)
    try:
        restart_channels()
        _restart_gateway("nxdngateway")
    except Exception as _e:
        print("NXDN ???????????: %r" % (_e,))
        return
    print("NXDN 参数已保存，NXDNGateway 已重载")


def save_dstar(a):
    gw_call = clean_call(a[0] if a else "")
    rep_call = clean_call(a[1] if len(a) > 1 else "") or gw_call
    module = (a[2] if len(a) > 2 and a[2] else "B").upper()[:1]
    irc_user = a[3] if len(a) > 3 else ""
    irc_pw = a[4] if len(a) > 4 else ""
    reflector = a[7] if len(a) > 7 else ""
    startup = a[8] if len(a) > 8 else "1"
    routing = a[9] if len(a) > 9 else "1"
    dplus = a[11] if len(a) > 11 else ""
    ircddb_host = a[12] if len(a) > 12 else ""
    set_identity(gw_call)
    values = {
        "gatewayCallsign": gw_call,
        "repeaterCall1": rep_call,
        "repeaterBand1": module,
        "ircddbUsername": irc_user,
        "ircddbPassword": irc_pw,
        "ircddbEnabled": routing or "1",
        # [FIX 1.0.87] 必须开机自动链接 + 掉线自动重连；
        # 原来写 atStartup1=0 会让热点永远不连反射器（D-STAR 完全不通）。
        "atStartup1": "1",
        "reconnect1": "1",
    }
    if reflector:
        values["reflector1"] = reflector
    if ircddb_host:
        values["ircddbHostname"] = ircddb_host
    if str(dplus).strip() in ("0", "1"):
        values["dplusEnabled"] = str(dplus)
    set_flat(IRCDDB, values)
    # Host 侧 D-Star 模块号必须与网关 repeaterBand1 同步
    for n, item in plan_by_channel().items():
        if str(item.get("mode", "")).upper() != "D-STAR":
            continue
        hp = "/etc/MMDVM-Host-%d.ini" % n
        h = ini(hp)
        set_sec(h, "D-Star", "Enable", "1")
        set_sec(h, "D-Star", "Module", module)
        set_sec(h, "D-Star Network", "Enable", "1")
        save_ini(h, hp)
    _dstar_host_module = module
    restart_channels()
    _restart_gateway("ircddbgatewayd")
    print("D-STAR 参数已保存，ircDDBGateway 已重载")


def save_fm(a):
    call = clean_call(a[0] if a else "")
    set_identity(call)
    ctcss = a[1] if len(a) > 1 and a[1] else "88.5"
    p = "/etc/MMDVM-Host.ini"
    cp = ini(p)
    for n in active_channels():
        hp = "/etc/MMDVM-Host-%d.ini" % n
        h = ini(hp)
        plan = json.load(open(PLAN, encoding="utf-8")) if os.path.exists(PLAN) else {}
        item = next((x for x in plan.get("list", []) if int(x.get("n", 0)) == n), {})
        if str(item.get("mode", "")).upper() != "FM":
            continue
        set_sec(h, "FM", "Enable", "1")
        set_sec(h, "FM", "CTCSSFrequency", ctcss)
        set_sec(h, "FM", "AccessMode", "1")
        set_sec(h, "FM Network", "Enable", "1")
        set_sec(h, "FM Network", "GatewayAddress", "127.0.0.1")
        set_sec(h, "FM Network", "GatewayPort", "49880")
        save_ini(h, hp)
    set_sec(cp, "FM", "Enable", "1")
    save_ini(cp, p)
    restart_channels()
    print("FM 参数已保存")


def save_pocsag(a):
    p = "/etc/MMDVM-Host.ini"
    cp = ini(p)
    set_sec(cp, "POCSAG", "Enable", "1")
    set_sec(cp, "POCSAG", "Frequency", a[0] if a else "439500000")
    save_ini(cp, p)
    restart_channels()
    print("POCSAG 参数已保存")


def cmd_bminfo():
    cp = ini(DMRGW)
    sec = "DMR Network 1"
    out = {"callsign": cp.get("General", "Callsign", fallback=""),
           "id": cp.get("General", "Id", fallback=""),
           "host": cp.get(sec, "Address", fallback=""),
           "port": cp.get(sec, "Port", fallback="62031"),
           "enabled": cp.get(sec, "Enabled", fallback="0"),
           "ready": os.path.exists(DMR_READY)}
    print(json.dumps(out, ensure_ascii=False))


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "").upper()
    args = sys.argv[2:]
    try:
        if mode == "BMINFO": cmd_bminfo()
        elif mode == "RF": save_rf(args)
        elif mode == "DMR": save_dmr(args)
        elif mode == "YSF": save_ysf(args)
        elif mode == "P25": save_p25(args)
        elif mode == "NXDN": save_nxdn(args)
        elif mode == "D-STAR": save_dstar(args)
        elif mode == "FM": save_fm(args)
        elif mode == "POCSAG": save_pocsag(args)
        else:
            raise ValueError("未知保存模式: %s" % mode)
        return 0
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())