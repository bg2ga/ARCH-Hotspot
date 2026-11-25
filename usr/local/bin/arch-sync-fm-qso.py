#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep the local FMO bridge's QSO channel in sync with the live multi-channel plan."""
import json, os, shutil, subprocess, sys, time

PLAN = '/etc/mmdvm/channels.json'
CFG = '/opt/fmo-bridge/config.json'
INI_EDIT = '/usr/local/libexec/ini-edit.py'


def run(args, timeout=30):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return None


def active(unit):
    r = run(['systemctl', 'is-active', unit], 5)
    return bool(r and r.stdout.strip() == 'active')


def load_plan():
    try:
        with open(PLAN, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def pick_fm(plan):
    limit = int(plan.get('channels') or 0)
    candidates = []
    for item in plan.get('list') or []:
        try:
            n = int(item.get('n'))
        except Exception:
            continue
        if limit and n > limit:
            continue
        if str(item.get('mode') or '').upper() == 'FM':
            candidates.append((n, item))
    for n, item in candidates:
        if active('mmdvm-iq@%d.service' % n) and active('mmdvmhost@%d.service' % n):
            return n, item
    return candidates[0] if candidates else (None, None)


def write_cfg(cfg, plan, n, item):
    fm = cfg.setdefault('fm', {})
    fm['channel'] = int(n)
    try:
        if item.get('tx') is not None:
            fm['tx_mhz'] = round(float(item['tx']) / 1e6, 4)
    except Exception:
        pass
    try:
        if item.get('rx') is not None:
            fm['rx_mhz'] = round(float(item['rx']) / 1e6, 4)
    except Exception:
        pass
    bak = CFG + '.bak.sync-channel-' + time.strftime('%Y%m%d-%H%M%S')
    try:
        shutil.copy2(CFG, bak)
    except Exception:
        pass
    tmp = CFG + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, CFG)


def fix_host_gateway(n):
    path = '/etc/MMDVM-Host-%d.ini' % n
    if not os.path.exists(path):
        return False
    changed = False
    for key, value in (('GatewayAddress', '127.0.0.1'), ('GatewayPort', '49880')):
        r = run(['python3', INI_EDIT, path, 'set', 'FM Network', key, value], 15)
        if r is None or r.returncode != 0:
            continue
    try:
        text = open(path, encoding='utf-8', errors='ignore').read()
        changed = ('GatewayAddress=127.0.0.1' in text and 'GatewayPort=49880' in text)
    except Exception:
        pass
    return changed


def main():
    plan = load_plan()
    n, item = pick_fm(plan)
    if n is None:
        run(['systemctl', 'stop', 'fmo-bridge.service'], 30)
        print('FM_QSO_CHANNEL=none; fmo-bridge stopped')
        return 0
    try:
        with open(CFG, encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        print('FM_QSO_SYNC_ERROR=%s' % e)
        return 1
    write_cfg(cfg, plan, n, item or {})
    fix_host_gateway(n)
    run(['systemctl', 'enable', '--now', 'fmo-bridge.service'], 40)
    run(['systemctl', 'restart', 'fmo-bridge.service'], 40)
    print('FM_QSO_CHANNEL=%d' % n)
    return 0

if __name__ == '__main__':
    sys.exit(main())
