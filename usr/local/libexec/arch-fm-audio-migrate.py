#!/usr/bin/env python3
"""ARCH settings migration guard.
Preserves all user settings except the explicit 1.0.106 FM +50% migration.
Only the known default 0.45 is changed to 0.675; custom values are untouched.
"""
import json, os, shutil, time
p='/opt/fmo-bridge/config.json'
try:
    d=json.load(open(p, encoding='utf-8'))
except Exception:
    raise SystemExit(0)
old=d.get('fm',{}).get('txgain')
try:
    oldf=float(old)
except Exception:
    raise SystemExit(0)
if abs(oldf-0.45) < 1e-9:
    bak=p+'.bak-fmgain106-'+time.strftime('%Y%m%d-%H%M%S')
    shutil.copy2(p,bak)
    d['fm']['txgain']=0.675
    json.dump(d,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
    print('FM txgain migrated 0.45 -> 0.675; backup',bak)
else:
    print('FM txgain preserved:',old)
