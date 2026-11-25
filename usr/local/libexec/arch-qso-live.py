#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small event-only QSO state machine.

Only MMDVM-Host MQTT start/end/idle events may set or clear a live red state.
The journal/history path is deliberately not used here.
"""
import json
import os
import re
import subprocess
import threading
import time

try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None

OUT = "/run/arch-qso-live.json"
LOCK = threading.Lock()
LIVE = {}
# [FIX 1.0.132] finished entries are kept in the output for a while so the
# QSO record (call/target) stays visible after the red live state goes off.
RECENT = {}
RECENT_TTL = 60.0
CHANNELS = []
CHANNEL_BY_N = {}


def mode_norm(value):
    v = str(value or "").strip().upper().replace("_", "-")
    if v in ("D-STAR", "DSTAR", "D STAR"):
        return "D-STAR"
    return v


def load_channels():
    global CHANNELS, CHANNEL_BY_N
    rows = []
    try:
        plan = json.load(open("/etc/mmdvm/channels.json", encoding="utf-8"))
        n = int(plan.get("channels") or 0)
        for item in (plan.get("list") or [])[:n]:
            try:
                ch = int(item.get("n"))
            except Exception:
                continue
            mode = mode_norm(item.get("mode"))
            if mode and mode != "NONE":
                rows.append((ch, mode))
    except Exception:
        rows = [(i, "") for i in range(1, 13)]
    CHANNELS = [x[0] for x in rows]
    CHANNEL_BY_N = {x[0]: x[1] for x in rows}


def infer_channel(mode):
    mode = mode_norm(mode)
    found = [n for n, m in CHANNEL_BY_N.items() if m == mode]
    if len(found) == 1:
        return found[0]
    with LOCK:
        active = [n for n in found if any(k.startswith("CH%d|%s|" % (n, mode)) for k in LIVE)]
    return active[0] if len(active) == 1 else 0


def topic_channel(topic, mode=""):
    m = re.match(r"^mmdvmhost[-_]?(\d+)/json$", topic or "")
    if m:
        return int(m.group(1))
    if topic == "mmdvmhost/json":
        return infer_channel(mode)
    return 0


def clean_name(value):
    s = re.sub(r"s+", " ", str(value or "").strip())
    return s.split(" ")[0] if s else ""


def target(mode, ev):
    if mode == "DMR":
        dst = ev.get("dst_id")
        kind = "TG" if str(ev.get("group") or "").lower() in ("yes", "true", "1") else "PC"
        return "%s %s" % (kind, dst) if dst is not None else "-"
    if mode == "D-STAR":
        return str(ev.get("dst_callsign") or ev.get("reflector") or "-").strip() or "-"
    if mode == "YSF":
        return "DG-ID %s" % ev.get("dg-id", ev.get("dg_id", "-"))
    if mode in ("P25", "NXDN"):
        dst = ev.get("dst_id", "-")
        kind = "TG" if str(ev.get("group") or "").lower() in ("yes", "true", "1") else "PC"
        return "%s %s" % (kind, dst)
    return "FMO" if mode == "FM" else "-"


def source(mode, ev, fm_state=""):
    if mode == "FM":
        if "relaying_rf" in fm_state or "wait_rf" in fm_state:
            return "Net"
        if "relaying_ext" in fm_state or "wait_ext" in fm_state:
            return "RF"
        return "Local"
    s = str(ev.get("source") or "").lower()
    return "Net" if s in ("network", "net") else ("RF" if s == "rf" else "Local")


def call_for(mode, ev):
    if mode == "D-STAR":
        c = clean_name(ev.get("src_callsign")); x = clean_name(ev.get("src_ext"))
        return (c + ("/" + x if x else "")) if c else "-"
    if mode == "YSF":
        return clean_name(ev.get("src_callsign")) or "-"
    if mode in ("DMR", "P25", "NXDN"):
        return clean_name(ev.get("src_info")) or str(ev.get("src_id") or "-")
    return "FM语音" if mode == "FM" else "-"


def source_id(mode, ev):
    return str(ev.get("src_id") or "") if mode in ("DMR", "P25", "NXDN") else ""


def display_mode(mode, slot=0):
    if mode == "DMR":
        return "DMR S%d" % int(slot or 0)
    return "D-Star" if mode == "D-STAR" else mode


def key_for(ch, mode, slot):
    return "CH%d|%s|%s" % (int(ch or 0), mode_norm(mode), int(slot or 0))


def _prune_recent_locked():
    now = time.time()
    for k in list(RECENT):
        if now - float(RECENT[k].get("_ended", 0) or 0) > RECENT_TTL:
            RECENT.pop(k, None)


def save_locked():
    _prune_recent_locked()
    rows = [dict(v) for v in LIVE.values()]
    rows += [dict(v) for v in RECENT.values()]
    rows.sort(key=lambda q: (q.get("_updated", 0), q.get("ch", "")), reverse=True)
    tmp = OUT + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ok": True, "source": "mmdvm-host-mqtt", "updated": time.time(), "qso": rows}, f, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, OUT)
    except Exception:
        pass


def remove_locked(ch, mode="", slot=None):
    removed = []
    prefix = "CH%d|" % int(ch or 0)
    for k in list(LIVE):
        if not k.startswith(prefix):
            continue
        if mode and not k.startswith(prefix + mode_norm(mode) + "|"):
            continue
        if slot is not None and not k.endswith("|" + str(int(slot or 0))):
            continue
        e = LIVE.pop(k, None)
        if e:
            removed.append((k, e))
    return removed


def end_locked(ch, mode="", slot=None):
    # move the live entry into RECENT (kept RECENT_TTL seconds) and save
    now = time.time()
    for k, e in remove_locked(ch, mode, slot):
        e = dict(e)
        e["live"] = False
        dur = now - float(e.get("_started") or e.get("_updated") or now)
        e["duration"] = ("%.1fs" % dur) if dur > 0.05 else "\u5df2\u7ed3\u675f"
        e["_ended"] = now
        e["_updated"] = now
        RECENT[k] = e
    save_locked()


def upsert(ch, mode, ev, slot=0, fm_state=""):
    mode = mode_norm(mode)
    if not ch or not mode:
        return
    k = key_for(ch, mode, slot)
    with LOCK:
        old = LIVE.get(k) or {}
        now = time.time()
        call = call_for(mode, ev)
        if call == "-" and old.get("call"):
            call = old.get("call")
        row = {
            "time": time.strftime("%H:%M:%S"),
            "mode": display_mode(mode, slot),
            "_mode": mode,
            "ch": "CH%d" % int(ch),
            "slot": int(slot or 0),
            "src": source(mode, ev, fm_state),
            "call": call,
            "target": target(mode, ev),
            "duration": "通话中",
            "ber": ("%.1f%%" % float(ev.get("ber"))) if ev.get("ber") is not None else (old.get("ber") or "-"),
            "id": source_id(mode, ev) or old.get("id") or "",
            "call_valid": mode != "FM",
            "live": True,
            "_updated": now,
            "_started": old.get("_started") or now,
            "_event": str(ev.get("action") or fm_state or "live"),
        }
        if row["target"] == "-" and old.get("target"):
            row["target"] = old["target"]
        LIVE[k] = row
        save_locked()


def event_mode(obj):
    for k in ("DMR", "D-Star", "YSF", "P25", "NXDN", "FM"):
        if isinstance(obj.get(k), dict):
            return k, obj[k]
    return "", {}


def on_message(client, userdata, msg):
    try:
        obj = json.loads(msg.payload.decode("utf-8", "replace"))
    except Exception:
        return
    if not isinstance(obj, dict):
        return
    mode, ev = event_mode(obj)
    ch = topic_channel(msg.topic, mode)
    if isinstance(obj.get("Text"), dict):
        text = obj["Text"]
        if mode_norm(text.get("mode")) == "DMR":
            tch = ch or infer_channel("DMR")
            alias = clean_name(text.get("value"))
            if tch and alias:
                with LOCK:
                    k = key_for(tch, "DMR", text.get("slot") or 0)
                    if k in LIVE:
                        LIVE[k]["call"] = alias
                        LIVE[k]["_updated"] = time.time()
                        save_locked()
        return
    if isinstance(obj.get("MMDVM"), dict):
        if str(obj["MMDVM"].get("mode") or "") == "idle":
            tch = ch or 0
            if tch:
                with LOCK:
                    end_locked(tch)
        return
    if not mode or not ev:
        return
    ch = ch or infer_channel(mode)
    if not ch:
        return
    action = str(ev.get("action") or "").lower()
    slot = int(ev.get("slot") or 0)
    if mode == "FM":
        state = str(ev.get("state") or "").lower()
        if state in ("relaying_rf", "relaying_wait_rf", "relaying_ext", "relaying_wait_ext"):
            upsert(ch, mode, ev, 0, state)
        elif state in ("listening", "hang", "timeout_rf", "timeout_wait_rf", "timeout_ext", "timeout_wait_ext"):
            with LOCK:
                end_locked(ch, mode, 0)
        return
    if action in ("start", "late_entry"):
        upsert(ch, mode, ev, slot)
    elif action in ("end", "lost", "rejected"):
        with LOCK:
            end_locked(ch, mode, slot)


def on_connect(client, userdata, flags, rc, properties=None):
    # [FIX 1.0.132] every MMDVM-Host channel publishes under the default
    # MQTT Name "mmdvmhost", i.e. topic "mmdvmhost/json".  The old code only
    # subscribed mmdvmhostN/json and mmdvmhost-N/json, so no live event was
    # ever received -> /run/arch-qso-live.json stayed qso:[] forever and the
    # QSO list "talking" state never updated.
    client.subscribe("mmdvmhost/json", qos=0)
    for n in range(1, 13):
        client.subscribe("mmdvmhost%d/json" % n, qos=0)
        client.subscribe("mmdvmhost-%d/json" % n, qos=0)


def liveness_loop():
    # If a host unit stops/crashes without FME, clear that channel at once.
    while True:
        time.sleep(1.0)
        with LOCK:
            active_channels = sorted(set(int(k.split("|", 1)[0][2:]) for k in LIVE if k.startswith("CH")))
        for ch in active_channels:
            try:
                rc = subprocess.run(["systemctl", "is-active", "--quiet", "mmdvmhost@%d.service" % ch], timeout=3).returncode
            except Exception:
                rc = 1
            if rc != 0:
                with LOCK:
                    end_locked(ch)


def main():
    load_channels()
    with LOCK:
        save_locked()
    threading.Thread(target=liveness_loop, daemon=True, name="qso-liveness").start()
    if mqtt is None:
        return
    try:
        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "arch-qso-live", clean_session=True)
    except Exception:
        cli = mqtt.Client("arch-qso-live", clean_session=True)
    cli.on_connect = on_connect
    cli.on_message = on_message
    cli.connect_async("127.0.0.1", 1883, 30)
    cli.loop_forever()


if __name__ == "__main__":
    main()
