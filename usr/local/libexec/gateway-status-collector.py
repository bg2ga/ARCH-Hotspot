#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, re, time, threading, subprocess
try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None

OUT = "/run/arch-gateway-status.json"
lock = threading.Lock()
status = {}

MODES = {
    "dmr": "dmr-gateway",
    "ysf": "ysf-gateway",
    "p25": "p25-gateway",
    "nxdn": "nxdn-gateway",
    "dstar": "ircddb-gateway",
}

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _get_ini(path, section, key, first=False):
    try:
        cur = ""
        for ln in open(path, encoding="utf-8", errors="replace"):
            t = ln.strip()
            if t.startswith("[") and t.endswith("]"):
                cur = t[1:-1]
                continue
            if cur == section and t.lower().startswith(key.lower() + "="):
                v = t.split("=", 1)[1].strip()
                return (v.split(",")[0].strip() if first else v)
    except Exception:
        pass
    return ""

def seed():
    vals = {
        "dmr": _get_ini("/etc/DMRGateway.ini", "DMR Network 1", "Address"),
        "ysf": _get_ini("/etc/YSFGateway.ini", "Network", "Startup"),
        "p25": _get_ini("/etc/P25Gateway.ini", "Network", "Static", True),
        "nxdn": _get_ini("/etc/NXDNGateway.ini", "Network", "Static", True),
        "dstar": _get_ini("/etc/ircddbgateway", "", "reflector1").strip(),
    }
    for m, s in vals.items():
        set_state(m, "waiting", s, "等待网关连接")

def save():
    tmp = OUT + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        os.replace(tmp, OUT)
    except Exception:
        pass

def set_state(mode, state, server="", detail=""):
    with lock:
        old = status.get(mode) or {}
        status[mode] = {
            "mode": mode.upper(),
            "state": state,
            "connected": state == "connected",
            "server": server or old.get("server") or "",
            "detail": detail or "",
            "updated": now(),
        }
        save()

def mode_from_topic(topic):
    for m, prefix in MODES.items():
        if topic.startswith(prefix + "/"):
            return m
    return ""

def parse_json_link(mode, obj):
    link = obj.get("link") or obj.get("data") or obj
    if not isinstance(link, dict):
        return
    action = str(link.get("action") or link.get("state") or "").lower()
    server = str(link.get("reflector") or link.get("talkgroup") or link.get("network") or
                 link.get("master") or link.get("address") or "").strip()
    if mode == "dmr" and server and "." not in server:
        server = (status.get("dmr") or {}).get("server") or server
    if action in ("linked", "connected", "up", "success"):
        set_state(mode, "connected", server, "link action: " + action)
    elif action in ("unlinked", "disconnected", "down", "failed", "error"):
        set_state(mode, "disconnected", server, "link action: " + action)
    elif action in ("linking", "connecting", "reconnecting", "startup"):
        # The gateway emits action=linking when it has selected and opened
        # the target link. Treat it as connected for the status page.
        set_state(mode, "connected", server, "已选择并连接目标")

def parse_log(mode, text):
    for line in str(text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if mode == "dstar":
            m = re.search(r'DCS link to (.*?) (?:established|connected)', s, re.I)
            if m: set_state("dstar", "connected", m.group(1).strip(), "DCS 已连接"); continue
            m = re.search(r'DCS ACK message received from (.*)', s, re.I)
            if m: set_state("dstar", "connected", m.group(1).strip(), "收到 DCS 应答"); continue
            if re.search(r'DCS.*(?:link|connect).*(?:lost|failed|timeout)', s, re.I):
                set_state("dstar", "disconnected", "", "DCS 已断开"); continue
            m = re.search(r'Linking .* to (.*)', s, re.I)
            if m: set_state("dstar", "waiting", m.group(1).strip(), "正在连接"); continue
            continue
        if mode == "dmr":
            if re.search(r'received (?:network|rf) voice header|received network data|Logged into', s, re.I):
                set_state("dmr", "connected", "", "DMR 网络已连接"); continue
            if re.search(r'Closing DMR Network|Login failed|network watchdog has expired', s, re.I):
                set_state("dmr", "disconnected", "", "DMR 网络已断开"); continue
            if re.search(r'Opening DMR Network|DMR, Opening', s, re.I):
                set_state("dmr", "waiting", "", "DMR 正在连接"); continue
            continue
        if mode in ("ysf", "p25", "nxdn"):
            m = re.search(r'Statically linked to (?:reflector\s+)?([^\s]+)', s, re.I)
            if m: set_state(mode, "connected", m.group(1).strip(), "静态链接"); continue
            m = re.search(r'Linked to\s+([^\s]+)', s, re.I)
            if m: set_state(mode, "connected", m.group(1).strip(), "已连接"); continue
            m = re.search(r'Switched to (?:reflector|talkgroup)\s+([^\s]+)', s, re.I)
            if m: set_state(mode, "connected", m.group(1).strip(), "已连接"); continue
            if re.search(r'Unlinking from .* due to inactivity', s, re.I):
                continue
            m = re.search(r'Unlinking from (?:reflector\s+)?([^\s]+)(?:\s+by\s+.*)?', s, re.I)
            if m: set_state(mode, "disconnected", m.group(1).strip(), "已断开"); continue


def dmr_host_unit():
    try:
        plan=json.load(open("/etc/mmdvm/channels.json",encoding="utf-8"))
        ch=int(plan.get("channels") or 0)
        for x in (plan.get("list") or [])[:ch]:
            if str(x.get("mode") or "").upper()=="DMR":
                return "mmdvmhost@%s" % x.get("n")
    except Exception:
        pass
    return "mmdvmhost@1"


def unit_for_mode(mode):
    # [FIX 1.0.131] DMR 的"已连接"应该看 DMRGateway 与 BrandMeister 的链路，
    # 即 DMRGateway 日志里的 "Logged into the master successfully"。
    # 旧实现去读 mmdvmhost@N 的日志，那里根本没有这行，
    # 所以 DMR 永远停在"等待网关连接"。
    if mode == "dmr":
        return "dmrgateway"
    return {"ysf":"ysfgateway","p25":"p25gateway","nxdn":"nxdngateway","dstar":"ircddbgatewayd"}.get(mode,"")

def poll_loop():
    while True:
        for mode in ("dmr","ysf","p25","nxdn","dstar"):
            unit=unit_for_mode(mode)
            if not unit:
                continue
            try:
                if mode == "dmr":
                    # 先读 MMDVM-Host（反映射频侧活动），再读 DMRGateway（链路状态优先）。
                    hu = dmr_host_unit()
                    if hu and hu != unit:
                        r2=subprocess.run(["journalctl","-u",hu,"-n","240","--no-pager"],capture_output=True,text=True,timeout=8)
                        parse_log(mode,r2.stdout or "")
                r=subprocess.run(["journalctl","-u",unit,"-n","240","--no-pager"],capture_output=True,text=True,timeout=8)
                parse_log(mode,r.stdout or "")
            except Exception:
                pass
            if mode in ("ysf", "p25", "nxdn") and (status.get(mode) or {}).get("state") != "connected":
                try:
                    ar=subprocess.run(["systemctl","is-active","--quiet",unit],timeout=5)
                    if ar.returncode == 0:
                        if mode == "ysf":
                            target=_get_ini("/etc/YSFGateway.ini","Network","Startup")
                        elif mode == "p25":
                            target=_get_ini("/etc/P25Gateway.ini","Network","Static",True)
                        else:
                            target=_get_ini("/etc/NXDNGateway.ini","Network","Static",True)
                        if target:
                            set_state(mode,"connected",target,"已配置并运行")
                except Exception:
                    pass
            if mode == "dstar":
                cur=(status.get("dstar") or {})
                if cur.get("state") != "connected":
                    try:
                        ar=subprocess.run(["systemctl","is-active",unit],capture_output=True,text=True,timeout=5)
                        if (ar.stdout or "").strip() == "active":
                            srv=cur.get("server") or _get_ini("/etc/ircddbgateway", "", "reflector1").strip()
                            set_state("dstar", "connected", srv, "已配置并运行")
                    except Exception:
                        pass
        time.sleep(5)

def on_connect(client, userdata, flags, rc):
    for t in ("dmr-gateway/#", "ysf-gateway/#", "p25-gateway/#", "nxdn-gateway/#", "ircddb-gateway/#"):
        client.subscribe(t)

def on_message(client, userdata, msg):
    try:
        mode = mode_from_topic(msg.topic)
        if not mode:
            return
        text = msg.payload.decode("utf-8", "replace")
        if msg.topic.endswith("/json"):
            try: parse_json_link(mode, json.loads(text))
            except Exception: pass
        if msg.topic.endswith("/log"):
            parse_log(mode, text)
    except Exception:
        pass

def main():
    seed()
    threading.Thread(target=poll_loop,daemon=True).start()
    if mqtt is None:
        return
    try:
        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "arch-gateway-status", clean_session=True)
    except Exception:
        cli = mqtt.Client("arch-gateway-status", clean_session=True)
    cli.on_connect = on_connect
    cli.on_message = on_message
    cli.connect_async("127.0.0.1", 1883, 60)
    cli.loop_forever()

if __name__ == "__main__":
    main()
