#!/usr/bin/env python3
import configparser
import json
import os
import subprocess
from pathlib import Path

PLAN = '/etc/mmdvm/channels.json'
HOST_SRC = '/usr/local/etc/hosts'
HOST_DST = '/usr/share/ircddbgateway'

def run(*args):
    try:
        return subprocess.run(list(args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45).returncode
    except Exception:
        return 1

def ini(path):
    cp = configparser.RawConfigParser(); cp.optionxform = str
    try: cp.read(path, encoding='utf-8')
    except Exception: pass
    return cp

def get_ini(path, section, key, first=False):
    if section == '':
        try:
            for line in open(path, encoding='utf-8', errors='replace'):
                t=line.strip()
                if t.startswith('[') or '=' not in t: continue
                k,v=t.split('=',1)
                if k.strip()==key:
                    v=v.strip()
                    return v.split(',')[0].strip() if first else v
        except Exception:
            pass
        return ''
    cp = ini(path)
    try: v = cp.get(section, key, fallback='').strip()
    except Exception:
        try: v = cp.get('DEFAULT', key, fallback='').strip()
        except Exception: v = ''
    return v.split(',')[0].strip() if first else v

def set_root_key(path, key, value):
    try: lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError: return
    out=[]; done=False
    for line in lines:
        if line.strip().startswith(key + '='):
            out.append('%s=%s' % (key, value)); done=True
        else: out.append(line)
    if not done: out.append('%s=%s' % (key, value))
    Path(path).write_text('\n'.join(out) + '\n', encoding='utf-8')

def sync_hosts():
    try:
        os.makedirs(HOST_DST, exist_ok=True)
        for name in ('DCS_Hosts.txt','DPlus_Hosts.txt','DExtra_Hosts.txt','CCS_Hosts.txt','XLX_Hosts.txt'):
            src=os.path.join(HOST_SRC,name); dst=os.path.join(HOST_DST,name)
            if not os.path.isfile(src): continue
            try:
                if os.path.islink(dst) or os.path.exists(dst): os.unlink(dst)
                os.symlink(src,dst)
            except FileExistsError: pass
    except Exception: pass

def service_exists(name):
    # [1.0.161] 支持 dmrgateway@2 这类由模板生成的实例单元。
    if os.path.exists('/etc/systemd/system/%s.service' % name) or os.path.exists('/lib/systemd/system/%s.service' % name):
        return True
    if '@' in name:
        tmpl = name.split('@', 1)[0] + '@.service'
        return (os.path.exists('/etc/systemd/system/' + tmpl) or
                os.path.exists('/lib/systemd/system/' + tmpl))
    return False

def set_service(name, enabled):
    if not service_exists(name): return
    if enabled:
        run('systemctl','enable',name+'.service')
        run('systemctl','restart',name+'.service')
    else: run('systemctl','disable','--now',name+'.service')

def main():
    modes=set()
    dmr_channels=set()
    try:
        d=json.load(open(PLAN,encoding='utf-8')); ch=max(1,int(d.get('channels') or 1))
        for x in (d.get('list') or [])[:ch]:
            m=str(x.get('mode') or '').upper()
            if m in ('DMR','D-STAR','YSF','P25','NXDN','FM'): modes.add(m)
            if m == 'DMR':
                try: dmr_channels.add(int(x.get('n') or 0))
                except Exception: pass
    except Exception: pass
    dmr_ok=False
    try:
        dmr_id=get_ini('/etc/DMRGateway.ini','DMR Network 1','Id')
        dmr_ok=bool(dmr_id and any(c in '123456789' for c in dmr_id))
        if dmr_ok: Path('/etc/mmdvm/dmr-ready').touch()
        else:
            try: os.unlink('/etc/mmdvm/dmr-ready')
            except FileNotFoundError: pass
    except Exception: pass
    ysf_ok=bool(get_ini('/etc/YSFGateway.ini','Network','Startup'))
    p25_ok=bool(get_ini('/etc/P25Gateway.ini','Network','Static'))
    nxdn_ok=bool(get_ini('/etc/NXDNGateway.ini','Network','Static'))
    dstar_ok=bool(get_ini('/etc/ircddbgateway','','reflector1'))
    fm_ok=False
    try:
        f=json.load(open('/opt/fmo-bridge/config.json',encoding='utf-8'))
        server=f.get('server') or {}; client=f.get('client') or {}
        cert=client.get('user_cert') or ''; key=client.get('user_key') or ''
        cert=cert if os.path.isabs(cert) else os.path.join('/opt/fmo-bridge',cert)
        key=key if os.path.isabs(key) else os.path.join('/opt/fmo-bridge',key)
        fm_ok=bool(server.get('mqtt_host') and os.path.isfile(cert) and os.path.isfile(key))
    except Exception: pass
    if 'D-STAR' in modes and dstar_ok:
        set_root_key('/etc/ircddbgateway','atStartup1','1')
        set_root_key('/etc/ircddbgateway','reconnect1','1')
        set_root_key('/etc/ircddbgateway','logEnabled','1')
        sync_hosts()
    set_service('dmrgateway','DMR' in modes and dmr_ok)
    # [1.0.161] 每个额外 DMR 通道对应一个独立网关实例；移除通道时同步停用。
    dmr_active = ('DMR' in modes and dmr_ok)
    for n in range(2, 8):
        cfg = '/etc/DMRGateway-%d.ini' % n
        set_service('dmrgateway@%d' % n,
                    dmr_active and n in dmr_channels and os.path.exists(cfg))
    set_service('ysfgateway','YSF' in modes and ysf_ok)
    set_service('p25gateway','P25' in modes and p25_ok)
    set_service('nxdngateway','NXDN' in modes and nxdn_ok)
    set_service('ircddbgatewayd','D-STAR' in modes and dstar_ok)
    set_service('fmo-bridge','FM' in modes and fm_ok)
    set_service('arch-gateway-status',True)
    print('gateway autoconnect:', ' '.join(sorted(modes)) or 'NONE')

if __name__ == '__main__': main()
