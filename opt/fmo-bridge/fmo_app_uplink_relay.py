#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FMO APP uplink relay.

The phone APP sends one 80 ms PCM frame per MQTT packet under its own UID.
Real FMO devices normally receive a server stream: NOCALL/uid=487, vendor 0,
RADPCM, up to three 80 ms frames per message packet. This service converts
and aggregates APP uplink packets into that exact shape. Server-uid packets
are ignored, so the relay cannot loop into itself.
"""
import hashlib
import json
import sys
import threading
import time

sys.path.insert(0, '/opt/dmr-fmo-bridge')
import fmo_proto as FP          # noqa: E402
import fmo_packet as FPK        # noqa: E402
import fmo_voice as FV          # noqa: E402
from paho.mqtt.client import Client  # noqa: E402

BASE = '/opt/fmo-announcer'
CFG = json.load(open(BASE + '/config.json', encoding='utf-8'))
CERT = json.load(open('/opt/fmo-relay/pki/user_NOCALLRLY.json', encoding='utf-8'))
INTER = json.load(open('/opt/fmo-relay/pki/intermediate.json', encoding='utf-8'))
SEED = FP.b64u_decode(open('/opt/fmo-relay/pki/user_NOCALLRLY.key', encoding='ascii').read().strip())
SRV = CFG['server']
TOPIC = 'FMO/RAW'
SERVER_UID = int(SRV['uid'])
SERVER_CALL = str(SRV['callsign']).upper()
CLIENT_ID = 'fmo-app-uplink-relay'
MAX_FRAMES = 1
PARTIAL_MS = 180
STREAM_IDLE_S = 2.0

lock = threading.Lock()
streams = {}
seen = {}
codecs = {}
last_log = 0.0
out_count = 0
out_frames = 0
skip = 0
cli = None


def log(msg):
    print('[%s] %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg), flush=True)


def prune_seen(now):
    for k, t in list(seen.items()):
        if now - t > 30.0:
            seen.pop(k, None)
    if len(codecs) > 128:
        codecs.clear()


def get_codec(key):
    c = codecs.get(key)
    if c is None:
        c = FV.RadPcmCodec()
        codecs[key] = c
    return c


def publish_frames(key, st, frames):
    global out_count, out_frames, last_log
    if not frames:
        return 0
    sid, uid, call = key[1], key[0], st.get('call') or ''
    out = FPK.build_voice_packet(
        uid, call, frames,
        stream_begin_utc=sid,
        ts_ms=int(st.get('last_ts') or int(time.time() * 1000)),
        srv_uid=SERVER_UID,
        vendor=0,
        smeter=0,
        seq_index0=1,
    )
    seen[hashlib.sha1(out).digest()] = time.time()
    cli.publish(TOPIC, out, qos=0)
    out_count += 1
    out_frames += len(frames)
    now = time.time()
    if out_count == 1 or now - last_log >= 5.0:
        last_log = now
        log('relayed_packets=%d frames=%d source=%s/%s sid=%s packet_frames=%d' % (
            out_count, out_frames, call, uid, sid, len(frames)))
    return len(frames)


def flush_key(key, force=False):
    st = streams.get(key)
    if not st:
        return
    frames = st['frames']
    while len(frames) >= MAX_FRAMES:
        chunk = frames[:MAX_FRAMES]
        del frames[:MAX_FRAMES]
        publish_frames(key, st, chunk)
    if force and frames:
        # Flush the final partial packet after the input stream has paused.
        chunk = frames[:]
        frames.clear()
        publish_frames(key, st, chunk)
    if not frames and force:
        streams.pop(key, None)


def janitor():
    while True:
        time.sleep(0.04)
        now = time.time()
        with lock:
            for key, st in list(streams.items()):
                if st['frames'] and now - st['last_rx'] >= PARTIAL_MS / 1000.0:
                    flush_key(key, force=True)
                elif not st['frames'] and now - st['last_rx'] > STREAM_IDLE_S:
                    streams.pop(key, None)


def on_connect(client, userdata, flags, rc):
    log('MQTT connect rc=%s' % rc)
    if rc == 0:
        client.subscribe(TOPIC, qos=0)


def on_disconnect(client, userdata, rc):
    if rc:
        log('MQTT disconnected rc=%s' % rc)


def on_message(client, userdata, msg):
    global skip
    if msg.topic != TOPIC or len(msg.payload) < 64:
        return
    try:
        pkt = FPK.parse_voice_packet(bytes(msg.payload))
    except Exception:
        return
    uid = int(pkt.get('uid') or 0)
    if uid == SERVER_UID:
        skip += 1
        return
    if not pkt.get('crc_ok'):
        return
    in_frames = pkt.get('frames') or []
    if not in_frames:
        return
    vendor = int(pkt.get('vendor') or 0)
    if vendor == 0 and not any(cm == FPK.CM_PCM for cm, _ in in_frames):
        return
    now = time.time()
    digest = hashlib.sha1(bytes(msg.payload)).digest()
    if now - seen.get(digest, 0.0) < 30.0:
        return
    seen[digest] = now
    if len(seen) > 2048:
        prune_seen(now)

    sid = int(pkt.get('streamBeginUTC') or pkt.get('timestamp') or 0)
    key = (uid, sid)
    codec = get_codec(key)
    converted = []
    for cm, payload in in_frames:
        if cm == FPK.CM_PCM:
            try:
                enc = codec.encode(payload)
            except Exception as e:
                log('RADPCM encode failed uid=%s sid=%s: %s' % (uid, sid, e))
                return
            if not enc:
                return
            converted.append((FPK.CM_RADPCM, enc))
        elif cm in (FPK.CM_RADPCM, FPK.CM_OPUS):
            converted.append((cm, payload))
        else:
            return
    with lock:
        st = streams.get(key)
        if st is None:
            st = {'frames': [], 'started': False, 'last_rx': now, 'last_ts': int(pkt.get('timestamp') or 0), 'call': str(pkt.get('callsign') or '')}
            streams[key] = st
        st['frames'].extend(converted)
        st['last_rx'] = now
        st['last_ts'] = int(pkt.get('timestamp') or int(now * 1000))
        if not st.get('started') and st['frames']:
            first = st['frames'][:1]
            del st['frames'][:1]
            publish_frames(key, st, first)
            st['started'] = True
        flush_key(key, force=False)


def main():
    global cli
    login = FP.build_mqtt_login(
        CERT, INTER, SEED, SRV['callsign'], int(SRV['uid']),
        SRV['mqtt_host'], int(SRV['mqtt_port']), SRV['certFingerprint'], role='user')
    cli = Client(client_id=CLIENT_ID, protocol=3)
    cli.username_pw_set(login['username'], login['password'])
    cli.on_connect = on_connect
    cli.on_disconnect = on_disconnect
    cli.on_message = on_message
    cli.reconnect_delay_set(min_delay=1, max_delay=10)
    threading.Thread(target=janitor, daemon=True).start()
    while True:
        try:
            cli.connect(SRV['mqtt_host'], int(SRV['mqtt_port']), 30)
            cli.loop_forever()
        except Exception as e:
            log('error: %s; reconnecting' % e)
            time.sleep(5)


if __name__ == '__main__':
    main()
