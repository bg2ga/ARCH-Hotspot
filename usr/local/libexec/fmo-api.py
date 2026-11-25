# -*- coding: utf-8 -*-
"""ARCH FMO 本地桥接 Web 后端。

仅供本机 root 控制台通过 /usr/local/bin/web-fmo.sh 调用。
"""
import copy
from pathlib import Path
import glob
import json
import os
import re
import subprocess
import sys
import time

BASE = "/opt/fmo-bridge"
CONFIG = os.environ.get("ARCH_FMO_CONFIG", os.path.join(BASE, "config.json"))
HOST_INI = "/etc/MMDVM-Host-%d.ini"
DEFAULT_REG_HOST = "fmo.hljrsa.cn"
DEFAULT_REG_PORT = 8081
TONE_CFG = "/etc/mmdvm/fm-tailtone.json"

TONES = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
    94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3,
    131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
    171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
    203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
]
COMMON_TONES = [67.0, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
                94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8,
                123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7,
                162.2, 167.9, 173.8, 179.9, 186.2, 192.8, 203.5, 210.7,
                218.1, 225.7, 233.6, 241.8, 250.3, 254.1]

DEFAULT_CFG = {
    "server": {"callsign": "", "uid": 0, "mqtt_host": DEFAULT_REG_HOST,
               "mqtt_port": 1883, "reg_port": DEFAULT_REG_PORT,
               "certFingerprint": ""},
    "client": {"callsign": "", "uid": 0, "role": "user",
               "user_cert": "./pki/user_fm.json",
               "user_key": "./pki/user_fm.key",
               "intermediate_cert": "./pki/intermediate.json"},
    "fm": {"bind_addr": "127.0.0.1", "bind_port": 49880,
           "rxgain": 1.0, "txgain": 0.45, "vendor": 0, "debug": False,
           "tone": {"enable": False, "freq": 1000.0, "ms": 200,
                    "delay_ms": 0, "gain": 1.0},
           "ctcss": {"enable": True, "rx": True, "tx": True,
                     "freq": 88.5, "rx_tone": "88.5", "tx_tone": "88.5"},
           "dsp": {"enable": False, "expander": False, "expander_thr": 0.03,
                   "expander_ratio": 3.0, "agc": False,
                   "agc_target": 0.09, "agc_max_gain_db": 12.0,
                   "agc_min_gain_db": -26.0, "agc_down_ms": 12.0,
                   "agc_up_ms": 600.0}},
    "voice": {"codec": "radpcm", "topic": "FMO/RAW", "relay_to_public": False},
}


def _deep_merge(dst, src):
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def _abs(path):
    path = str(path or "")
    return path if os.path.isabs(path) else os.path.abspath(os.path.join(BASE, path))


def load_cfg():
    cfg = copy.deepcopy(DEFAULT_CFG)
    try:
        with open(CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _deep_merge(cfg, data)
    except (OSError, ValueError):
        pass
    # 兼容旧版遗留配置：通道号/固定频率永远不作为绑定项返回给页面。
    if isinstance(cfg.get("fm"), dict):
        for k in ("channel", "tx_mhz", "rx_mhz"):
            cfg["fm"].pop(k, None)
    return cfg


def save_cfg(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG)


def _run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return -1, "", str(e)


def _short_error(v, limit=220):
    s = str(v or "").strip().splitlines()
    msg = s[0].strip() if s else ""
    if len(msg) > limit:
        msg = msg[:limit] + "..."
    return msg or "未知错误"


def is_active(unit):
    rc, out, _ = _run(["systemctl", "is-active", unit], 8)
    return rc == 0 and out.strip() == "active"


def _ini_get(path, section, key, default=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return default
    sec = None
    key_l = key.lower()
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1].strip().lower()
            continue
        if sec != section.lower() or "=" not in line or line.lstrip().startswith(("#", ";")):
            continue
        k, v = line.split("=", 1)
        if k.strip().lower() == key_l:
            return v.split("#", 1)[0].strip()
    return default


def _ini_set(path, updates):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        raise RuntimeError("配置文件不存在: %s" % path)
    sections = {}
    current = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            current = s[1:-1].strip().lower()
            sections.setdefault(current, [i, i])
        elif current:
            sections[current][1] = i
    for section, values in updates.items():
        sl = section.lower()
        if sl not in sections:
            lines.append("\n[%s]\n" % section)
            start = len(lines)
            sections[sl] = [start, start]
        first, last = sections[sl]
        for key, val in values.items():
            pat = re.compile(r"^\s*" + re.escape(key) + r"\s*=", re.I)
            found = None
            for i in range(first + 1, last + 1):
                if pat.match(lines[i]):
                    found = i
                    break
            newline = "%s=%s\n" % (key, val)
            if found is None:
                lines.insert(last + 1, newline)
                last += 1
            else:
                lines[found] = newline
            sections[sl][1] = last
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, path)


def _squelch_thresholds(level):
    level = max(0, min(9, int(level)))
    high = 10 + 10 * level
    low = max(0, high - 10)
    return high, low


def _ensure_required_fm(cfg=None, n=None, squelch_level=None, need_tx=None):
    """Keep receive CTCSS enabled; TX CTCSS is explicit and defaults off."""
    cfg = cfg or load_cfg()
    fm = cfg.setdefault("fm", {})
    dsp = fm.setdefault("dsp", {})
    dsp.setdefault("enable", False)
    dsp.setdefault("expander", False)
    dsp.setdefault("agc", False)
    # 这里的 expander 是下行音频噪声门，独立可调，不能和 FM NoiseSquelch 混用。
    ct = fm.setdefault("ctcss", {})
    try:
        tone = float(ct.get("freq") or ct.get("rx_tone") or 88.5)
    except Exception:
        tone = 88.5
    if not (60.0 <= tone <= 260.0):
        tone = 88.5
    tone_s = ("%.1f" % tone).rstrip("0").rstrip(".")
    try:
        if squelch_level is None:
            squelch_level = int(((fm.get("squelch") or {}).get("level", 3)))
    except Exception:
        squelch_level = 3
    squelch_level = max(0, min(9, int(squelch_level)))
    sq_high, sq_low = _squelch_thresholds(squelch_level)
    fm["squelch"] = {"level": squelch_level, "high": sq_high, "low": sq_low}
    local_repeat = bool(fm.get("local_repeat", True))
    if need_tx is None:
        need_tx = bool(ct.get("tx_enabled", False))
    need_tx = bool(need_tx)
    ct.update({"enable": True, "rx": True, "tx": need_tx,
               "tx_enabled": need_tx, "freq": tone,
               "rx_tone": tone_s, "tx_tone": tone_s if need_tx else "off"})
    try:
        Path("/etc/mmdvm/fm-ctcss-tx.enable").write_text("1\n" if need_tx else "0\n", encoding="ascii")
        os.chmod("/etc/mmdvm/fm-ctcss-tx.enable", 0o666)
    except Exception:
        pass
    n = n or active_channel(cfg)
    if n:
        _ini_set(HOST_INI % n, {
            "General": {"RFModeHang": "10", "NetModeHang": "3"},
            "FM": {
            "Enable": "1",
            "CTCSSFrequency": tone_s,
            "CTCSSHighThreshold": "30",
            "CTCSSLowThreshold": "20",
            "CTCSSLevel": "2.0",
            "AccessMode": "1",
            "NoiseSquelch": "0",
            "SquelchHighThreshold": str(sq_high),
            "SquelchLowThreshold": str(sq_low),
            "AckFrequency": "1000",
            "AckMinTime": "0",
            "AckDelay": "120",
            "AckLevel": "70.0",
            "CallsignAtStart": "1",
            "CallsignAtEnd": "1",
            "CallsignAtLatch": "1",
            "LinkMode": "1",
            "RFAudioBoost": "1",
            "ExtAudioBoost": "1",
            },
            "FM Network": {"ModeHang": "3"},
            "Modem": {"FMTXLevel": "50"},
        })
    return cfg, n


def active_channel(cfg=None):
    """按当前 channels.json 的已启用通道实时找 FM，不保存固定通道号。"""
    try:
        with open("/etc/mmdvm/channels.json", encoding="utf-8") as f:
            plan = json.load(f)
        limit = max(1, min(16, int(plan.get("channels") or 1)))
        for item in plan.get("list", [])[:limit]:
            if str(item.get("mode", "")).upper() == "FM":
                n = int(item.get("n", 0))
                if 1 <= n <= 16 and os.path.exists(HOST_INI % n):
                    return n
        return None
    except Exception:
        pass
    # 仅当规划文件不可读时才回退；仍然只认真正启用 FM 的 Host。
    for n in range(1, 17):
        p = HOST_INI % n
        if os.path.exists(p):
            en = _ini_get(p, "FM", "Enable", "0")
            if str(en).strip().lower() in ("1", "true", "yes", "on"):
                return n
    return None


def cert_info(cfg=None):
    cfg = cfg or load_cfg()
    p = _abs((cfg.get("client") or {}).get("user_cert", "./pki/user_fm.json"))
    out = {"exists": os.path.exists(p), "path": p}
    if not out["exists"]:
        return out
    try:
        with open(p, encoding="utf-8") as f:
            c = json.load(f)
        sub = c.get("subject") or {}
        out.update({"callsign": sub.get("callsign", ""), "uid": sub.get("uid"),
                    "notAfter": c.get("notAfter") or c.get("not_after") or ""})
    except Exception as e:
        out["error"] = str(e)
    return out


def summarize():
    cfg = load_cfg()
    cert = cert_info(cfg)
    if cert.get("exists"):
        cfg.setdefault("client", {})["callsign"] = cert.get("callsign", "")
        cfg["client"]["uid"] = cert.get("uid", 0)
    cfg["cert"] = cert
    n = active_channel(cfg)
    if n:
        try:
            with open("/etc/mmdvm/channels.json", encoding="utf-8") as f:
                plan = json.load(f)
            item = next(x for x in plan.get("list", []) if int(x.get("n", 0)) == n)
            cfg.setdefault("fm", {})["tx_mhz"] = round(float(item.get("tx") or 0) / 1e6, 5)
            cfg["fm"]["rx_mhz"] = round(float(item.get("rx") or 0) / 1e6, 5)
        except Exception:
            pass
    return cfg


def _current_ctcss(cfg=None, n=None):
    cfg = cfg or load_cfg()
    ct = (cfg.get("fm") or {}).get("ctcss") or {}
    n = n or active_channel(cfg)
    tone = ct.get("freq", ct.get("rx_tone", "off"))
    try:
        tone = float(tone)
    except Exception:
        tone = 0.0
    need_rx = True
    need_tx = False
    tx_level = 0.0
    try:
        squelch_level = int(((cfg.get("fm") or {}).get("squelch") or {}).get("level", 3))
    except Exception:
        squelch_level = 3
    squelch_level = max(0, min(9, squelch_level))
    if n:
        v = _ini_get(HOST_INI % n, "FM", "CTCSSFrequency")
        try:
            if v is not None:
                tone = float(v)
        except Exception:
            pass
        need_rx = str(_ini_get(HOST_INI % n, "FM", "AccessMode", "6")).strip() in ("1", "2", "3", "5", "6")
        try:
            tx_level = float(_ini_get(HOST_INI % n, "FM", "CTCSSLevel", "0") or 0)
        except Exception:
            tx_level = 0.0
        try:
            need_tx = Path("/etc/mmdvm/fm-ctcss-tx.enable").read_text(encoding="ascii").strip() != "0"
        except Exception:
            need_tx = bool(ct.get("tx_enabled", False))
        if ((cfg.get("fm") or {}).get("squelch") or {}).get("level") is None:
            try:
                squelch_level = int(_ini_get(HOST_INI % n, "FM", "SquelchHighThreshold", "30") or 30)
            except Exception:
                squelch_level = 3
            squelch_level = max(0, min(9, (squelch_level - 10) // 10))
    if tone <= 0:
        tone_s = "off"
    else:
        tone_s = ("%.1f" % tone).rstrip("0").rstrip(".")
    return {"tone": tone_s, "need_rx": need_rx, "need_tx": need_tx, "tx_level": tx_level, "squelch_level": squelch_level}


def cmd_status():
    cfg = summarize()
    n = active_channel(cfg)
    services = {
        "fmo-bridge": "active" if is_active("fmo-bridge.service") else "inactive",
    }
    if n:
        services["mmdvmhost"] = "active" if is_active("mmdvmhost@%d.service" % n) else "inactive"
    else:
        services["mmdvmhost"] = "inactive"
    return {"ok": True, "services": services, "cfg": cfg, "cert": cfg.get("cert", {})}


def cmd_ping(host, port):
    cfg = load_cfg()
    host = str(host or (cfg.get("server") or {}).get("mqtt_host") or DEFAULT_REG_HOST).strip()
    try:
        port = int(port or (cfg.get("server") or {}).get("reg_port") or DEFAULT_REG_PORT)
    except Exception:
        port = DEFAULT_REG_PORT
    sys.path.insert(0, BASE)
    import fmo_reg
    base = fmo_reg.register_base(host, port)
    info = fmo_reg.probe_server(base, timeout=15)
    return {"ok": bool(info.get("ok")), "info": info, "register_url": base}


def cmd_register(host, port, callsign, uid=0):
    callsign = str(callsign or "").strip().upper()
    if not callsign:
        return {"ok": False, "error": "请填写本机呼号"}
    cfg = load_cfg()
    host = str(host or (cfg.get("server") or {}).get("mqtt_host") or DEFAULT_REG_HOST).strip()
    try:
        port = int(port or (cfg.get("server") or {}).get("reg_port") or DEFAULT_REG_PORT)
    except Exception:
        port = DEFAULT_REG_PORT
    try:
        sys.path.insert(0, BASE)
        import fmo_reg
        base = fmo_reg.register_base(host, port)
        cert_path = _abs(cfg["client"].get("user_cert", "./pki/user_fm.json"))
        key_path = _abs(cfg["client"].get("user_key", "./pki/user_fm.key"))
        inter_path = _abs(cfg["client"].get("intermediate_cert", "./pki/intermediate.json"))
        server, user, inter = fmo_reg.ensure_cert(
            base, callsign, int(uid or 0), cert_path, key_path, inter_path,
            force=True, timeout=90, log=lambda *a: None)
        sub = (user or {}).get("subject") or {}
        s = cfg.setdefault("server", {})
        if server:
            s["callsign"] = server.get("callsign", s.get("callsign", ""))
            s["uid"] = server.get("uid", s.get("uid", 0))
            s["mqtt_host"] = server.get("mqttHost") or server.get("mqtt_host") or host
            s["mqtt_port"] = server.get("mqttPort") or server.get("mqtt_port") or 1883
            s["certFingerprint"] = server.get("certFingerprint") or s.get("certFingerprint", "")
        s["reg_port"] = port
        c = cfg.setdefault("client", {})
        c["callsign"] = sub.get("callsign", callsign)
        c["uid"] = sub.get("uid", 0)
        c["role"] = "user"
        _ensure_required_fm(cfg)
        save_cfg(cfg)
        _run(["systemctl", "daemon-reload"], 10)
        _run(["systemctl", "enable", "fmo-bridge.service"], 15)
        rc, out, err = _run(["systemctl", "restart", "fmo-bridge.service"], 30)
        if rc == 0:
            msg = "注册成功：%s / UID %s" % (c.get("callsign") or callsign, c.get("uid", "-"))
        else:
            msg = "注册成功：%s，但桥接启动失败" % (c.get("callsign") or callsign)
        return {"ok": rc == 0, "client": c, "server": s, "message": msg, "error": "" if rc == 0 else _short_error(err or out)}
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if "name or service" in low or "temporary failure" in low or "gaierror" in low:
            msg = "无法解析 FMO 注册服务器，请先检查网络或 DNS 是否可用"
        elif "timed out" in low or "timeout" in low:
            msg = "连接 FMO 注册服务器超时，请检查当前网络是否可访问外网"
        return {"ok": False, "error": _short_error(msg)}


def cmd_service(action="restart", what="bridge"):
    action = str(action or "").lower().strip()
    what = str(what or "bridge").lower().strip()
    if what not in ("bridge", "fmo-bridge", "all"):
        return {"ok": False, "error": "只支持控制 FMO 桥接"}
    unit = "fmo-bridge.service"
    if action in ("start", "restart") and not cert_info().get("exists"):
        return {"ok": False, "error": "请先申请 FMO 证书"}
    if action in ("start", "restart"):
        cfg = load_cfg()
        _ensure_required_fm(cfg)
        save_cfg(cfg)
    if action == "start":
        _run(["systemctl", "enable", unit], 15)
        rc, out, err = _run(["systemctl", "start", unit], 40)
    elif action == "stop":
        rc, out, err = _run(["systemctl", "stop", unit], 40)
    elif action == "restart":
        _run(["systemctl", "enable", unit], 15)
        rc, out, err = _run(["systemctl", "restart", unit], 40)
    else:
        return {"ok": False, "error": "未知操作"}
    return {"ok": rc == 0, "action": action, "unit": unit,
            "error": "" if rc == 0 else _short_error(err or out)}


def cmd_unregister():
    cfg = load_cfg()
    c = cfg.setdefault("client", {})
    removed = []
    for key, default in (("user_cert", "./pki/user_fm.json"),
                         ("user_key", "./pki/user_fm.key"),
                         ("intermediate_cert", "./pki/intermediate.json")):
        p = _abs(c.get(key, default))
        if os.path.exists(p):
            try:
                os.remove(p)
                removed.append(p)
            except OSError as e:
                return {"ok": False, "error": str(e)}
    c["callsign"] = ""
    c["uid"] = 0
    cfg.pop("cert", None)
    save_cfg(cfg)
    _run(["systemctl", "disable", "--now", "fmo-bridge.service"], 40)
    return {"ok": True, "removed": removed, "message": "本机 FMO 证书已注销，桥接已停止"}


def cmd_save(body):
    try:
        d = json.loads(body or "{}")
    except Exception as e:
        return {"ok": False, "error": "参数错误: %s" % e}
    cfg = load_cfg()
    if isinstance(d.get("fm"), dict):
        for k in ("channel", "tx_mhz", "rx_mhz"):
            d["fm"].pop(k, None)
    _deep_merge(cfg, d)
    # FMO 部署统一使用 RADPCM；OPUS 仅保留在实验代码中，不作为用户选项。
    v=cfg.setdefault("voice",{})
    v["codec"]="radpcm"
    v["topic"]="FMO/RAW"
    v["relay_to_public"]=False
    cfg["app_relay_enabled"]=False
    _ensure_required_fm(cfg)
    cert = cert_info(cfg)
    if cert.get("exists"):
        cfg.setdefault("client", {})["callsign"] = cert.get("callsign", "")
        cfg["client"]["uid"] = cert.get("uid", 0)
    save_cfg(cfg)
    return {"ok": True, "cfg": summarize()}


def cmd_restart(what):
    what = str(what or "bridge").lower()
    units = []
    if what in ("bridge", "all", "fmo-bridge"):
        units.append("fmo-bridge.service")
    if what in ("host", "all", "mmdvmhost", "mmdvm"):
        n = active_channel()
        if n:
            units.append("mmdvmhost@%d.service" % n)
    if "dmrgateway" in what:
        units.append("dmrgateway.service")
    done = []
    errors = []
    for u in units:
        rc, out, err = _run(["systemctl", "restart", u], 40)
        if rc == 0:
            done.append(u)
        else:
            errors.append("%s: %s" % (u, err or out))
    return {"ok": not errors, "restarted": done, "error": "; ".join(errors)}


def cmd_ctcss():
    return {"ok": True, "tones": TONES, "common": COMMON_TONES,
            "current": _current_ctcss()}


def cmd_apply_ctcss(body):
    try:
        d = json.loads(body or "{}")
        tone = str(d.get("tone", "88.5")).strip().lower()
        need_tx = bool(d.get("need_tx", False))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    n = active_channel()
    if not n:
        return {"ok": False, "error": "未找到可用的 FM 通道"}
    try:
        squelch_level = max(0, min(9, int(d.get("squelch_level", 3))))
    except Exception:
        squelch_level = 3
    if tone in ("off", "0", ""):
        return {"ok": False, "error": "接收亚音已强制开启，不能关闭；请选择 67.0–254.1Hz"}
    try:
        freq = float(tone)
        if freq < 60.0 or freq > 260.0:
            raise ValueError()
    except Exception:
        return {"ok": False, "error": "哑音频率无效"}
    tone_value = ("%.1f" % freq).rstrip("0").rstrip(".")
    cfg = load_cfg()
    cfg.setdefault("fm", {})["ctcss"] = {
        "enable": True, "rx": True, "tx": need_tx, "tx_enabled": need_tx,
        "freq": freq, "rx_tone": tone_value,
        "tx_tone": tone_value if need_tx else "off"}
    try:
        _ensure_required_fm(cfg, n, squelch_level, need_tx)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    save_cfg(cfg)
    rc, out, err = _run(["systemctl", "restart", "mmdvmhost@%d.service" % n], 40)
    if rc != 0:
        return {"ok": False, "error": _short_error(err or out), "current": _current_ctcss(cfg, n)}
    return {"ok": True, "channel": n, "current": _current_ctcss(cfg, n)}


def _tailtone_current(cfg=None, n=None):
    cfg = cfg or load_cfg()
    t = ((cfg.get("fm") or {}).get("tone") or {})
    n = n or active_channel(cfg)
    out = {"ok": True, "channel": n, "enable": bool(t.get("enable", False)),
           "freq": float(t.get("freq", 1000.0)), "ms": int(t.get("ms", 200)),
           "delay_ms": int(t.get("delay_ms", 120)), "gain": float(t.get("gain", 0.5))}
    if n:
        p = HOST_INI % n
        out["channel"] = n
    return out


def cmd_tailtone_set(body):
    try:
        d = json.loads(body or "{}")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    n = active_channel()
    if not n:
        return {"ok": False, "error": "未找到正在运行的 FM 通道"}
    try:
        enable = bool(d.get("enable", True))
        freq = int(float(d.get("freq", 1000)))
        ms = int(float(d.get("ms", 200)))
        delay = int(float(d.get("delay_ms", 120)))
        gain = float(d.get("gain", 0.7))
    except Exception:
        return {"ok": False, "error": "尾音参数格式错误"}
    freq = max(200, min(4000, int(round(freq / 10.0) * 10)))
    ms = max(40, min(2000, int(round(ms / 10.0) * 10)))
    delay = max(0, min(5000, int(round(delay / 10.0) * 10)))
    gain = max(0.0, min(1.0, gain))
    values = {
        "RFAck": "N",
        "AckFrequency": str(freq),
        "AckSpeed": str(max(4, min(200, ms // 10))),
        "AckDelay": "120",
        "AckMinTime": "0",
        "AckLevel": "70.0",
    }
    targets = []
    primary = HOST_INI % n
    if os.path.exists(primary):
        targets.append(primary)
    for path in sorted(glob.glob("/etc/MMDVM-Host-*.ini")):
        if path not in targets:
            targets.append(path)
    try:
        for path in targets:
            _ini_set(path, {"FM": values})
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        with open(TONE_CFG, "w", encoding="utf-8") as f:
            json.dump({"enable": enable, "freq": freq, "ms": ms,
                       "delay_ms": delay, "gain": gain}, f,
                      ensure_ascii=False, indent=2)
        os.chmod(TONE_CFG, 0o644)
    except Exception as e:
        return {"ok": False, "error": "写入尾音配置失败: %s" % e}
    cfg = load_cfg()
    cfg.setdefault("fm", {})["tone"] = {"enable": enable, "freq": freq,
        "ms": ms, "delay_ms": delay, "gain": gain}
    save_cfg(cfg)
    restarted = []
    errors = []
    for unit in ("mmdvmhost@%d.service" % n, "fmo-bridge.service"):
        rc, out, err = _run(["systemctl", "restart", unit], 40)
        if rc == 0:
            restarted.append(unit)
        else:
            errors.append("%s: %s" % (unit, err or out))
    if errors:
        return {"ok": False, "error": "; ".join(errors), "restarted": restarted}
    return {"ok": True, "channel": n, "restarted": restarted,
            "applied": {"freq": freq, "ms": ms, "delay_ms": delay, "gain": gain}}

def cmd_tone_test():
    try:
        with open("/tmp/fmo_tone_req", "w", encoding="ascii") as f:
            f.write(str(time.time()))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "hint": "已向本机 FMO 桥接发送尾音测试请求"}


def cmd_logs(lines):
    cnt = max(10, min(500, int(lines or 150)))
    rc, out, err = _run(["journalctl", "-u", "fmo-bridge.service",
                         "-n", str(cnt), "--no-pager"], 20)
    n = active_channel()
    if rc == 0 and out:
        return {"ok": True, "channel": n, "service": "fmo-bridge", "text": out}
    if n:
        rc, out2, err2 = _run(["journalctl", "-u", "mmdvmhost@%d.service" % n,
                               "-n", str(cnt), "--no-pager"], 20)
        if rc == 0:
            return {"ok": True, "channel": n,
                    "service": "mmdvmhost@%d" % n, "text": out2}
    return {"ok": False, "error": err or err2 or "无法读取日志", "text": out or out2}


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    if cmd == "status":
        out = cmd_status()
    elif cmd == "ping":
        out = cmd_ping(a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else DEFAULT_REG_PORT)
    elif cmd == "register":
        out = cmd_register(a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else DEFAULT_REG_PORT,
                           a[3] if len(a) > 3 else "", a[4] if len(a) > 4 else 0)
    elif cmd == "unregister":
        out = cmd_unregister()
    elif cmd == "service":
        out = cmd_service(a[1] if len(a) > 1 else "restart", a[2] if len(a) > 2 else "bridge")
    elif cmd == "save":
        out = cmd_save(a[1] if len(a) > 1 else "{}")
    elif cmd == "restart":
        out = cmd_restart(a[1] if len(a) > 1 else "all")
    elif cmd == "ctcss":
        out = cmd_ctcss()
    elif cmd == "apply-ctcss":
        out = cmd_apply_ctcss(a[1] if len(a) > 1 else "{}")
    elif cmd == "tailtone":
        out = _tailtone_current()
    elif cmd == "tailtone-set":
        out = cmd_tailtone_set(a[1] if len(a) > 1 else "{}")
    elif cmd == "tone-test":
        out = cmd_tone_test()
    elif cmd == "logs":
        out = cmd_logs(a[1] if len(a) > 1 else 150)
    else:
        out = {"ok": False, "error": "未知操作: %s" % cmd}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps({"ok": False, "error": _short_error(e)}, ensure_ascii=False))
        raise SystemExit(0)





