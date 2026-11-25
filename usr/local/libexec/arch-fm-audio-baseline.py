#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe FM baseline guard. Add missing keys only; never reset user settings."""
import configparser, glob, json, os, re, tempfile

MARKER = '/etc/mmdvm/fm-audio-baseline-v6'
HOSTS = '/etc/MMDVM-Host-*.ini'
CFG = '/opt/fmo-bridge/config.json'

def normalize_equals(path):
    try:
        lines = open(path, encoding='utf-8').readlines()
        out=[]; changed=False
        for line in lines:
            s=line.rstrip('\n')
            m=re.match(r'^(\s*[A-Za-z0-9_.-]+\s*)=\s*(.*)$', s)
            if m:
                n=m.group(1).rstrip()+'='+m.group(2).lstrip()
                changed = changed or (n != s)
                out.append(n+'\n')
            else: out.append(line)
        if changed:
            fd,tmp=tempfile.mkstemp(prefix='arch-host-fmt-',dir=os.path.dirname(path)); os.close(fd)
            open(tmp,'w',encoding='utf-8').writelines(out); os.replace(tmp,path)
        return changed
    except Exception: return False

def repair_hosts():
    changed=False
    for path in glob.glob(HOSTS):
        changed = normalize_equals(path) or changed
        cp=configparser.ConfigParser(); cp.optionxform=str; cp.read(path,encoding='utf-8')
        if not cp.has_section('FM') or cp.get('FM','Enable',fallback='0').strip()!='1': continue
        defaults=[('FM','RFAck','N'),('FM','ExtAck','N'),('FM','AckLevel','0.0'),
                  ('FM','CallsignAtStart','0'),('FM','CallsignAtEnd','0'),('FM','CallsignAtLatch','0'),
                  ('FM','CTCSSLevel','2.0'),('Modem','FMTXLevel','50'),('FM','ExtAudioBoost','1')]
        for sec,key,val in defaults:
            if not cp.has_option(sec,key): cp.set(sec,key,val); changed=True
        if changed: _save_cp(path,cp)
    return changed

def _save_cp(path, cp):
    fd,tmp=tempfile.mkstemp(prefix='arch-host-',dir=os.path.dirname(path)); os.close(fd)
    with open(tmp,'w',encoding='utf-8') as f: cp.write(f,space_around_delimiters=False)
    os.replace(tmp,path)

def repair_fmo():
    if not os.path.exists(CFG): return False
    with open(CFG,encoding='utf-8') as f: j=json.load(f)
    fm=j.setdefault('fm',{}); dsp=fm.setdefault('dsp',{}); changed=False
    defaults={'rxgain':1.0,'txgain':0.45}
    for k,v in defaults.items():
        if k not in fm: fm[k]=v; changed=True
    good={'enable':True,'expander':False,'expander_thr':0.01,'expander_ratio':3.0,'agc':False,
          'agc_target':0.08,'agc_max_gain_db':24.0,'agc_min_gain_db':-26.0,'agc_down_ms':12.0,
          'agc_up_ms':600.0,'hpf_hz':180.0,'shelf_db':0.0,'shelf_hz':1800.0,'limit':0.95,
          'agc_rms_ms':120.0,'agc_gate':0.004,'bass_cut_db':-4.0,'bass_cut_hz':250.0,
          'limiter_attack_ms':2.0,'limiter_release_ms':200.0,'peak_ceiling':0.99}
    for k,v in good.items():
        if k not in dsp: dsp[k]=v; changed=True
    ct=fm.setdefault('ctcss',{})
    if 'tx_enabled' not in ct:
        ct.update({'tx':False,'tx_enabled':False,'tx_tone':'off'}); changed=True
    for k,v in {'enable':True,'rx':True,'freq':88.5,'rx_tone':'88.5'}.items():
        if k not in ct: ct[k]=v; changed=True
    if changed:
        tmp=CFG+'.tmp'; open(tmp,'w',encoding='utf-8').write(json.dumps(j,ensure_ascii=False,indent=2)+'\n'); os.replace(tmp,CFG)
    return changed

changed=False
for _p in glob.glob(HOSTS): changed = normalize_equals(_p) or changed
changed = repair_hosts() or changed
changed = repair_fmo() or changed
os.makedirs(os.path.dirname(MARKER),exist_ok=True)
open(MARKER,'w',encoding='ascii').write('fm baseline v6: preserve user settings\n')
print('FM audio baseline repaired' if changed else 'FM audio baseline already OK')
