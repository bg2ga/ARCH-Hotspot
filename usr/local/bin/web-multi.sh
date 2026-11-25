#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import configparser, json, os, subprocess, sys
PLAN="/etc/mmdvm/channels.json"; MULTI="/etc/MMDVM-Multi.ini"; CS="/usr/local/libexec/channel-setup.py"; LIMIT="/usr/local/libexec/arch-channel-limit.py"

def run(cmd,timeout=60):
    try:return subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
    except Exception as e:return type("R",(),{"returncode":-1,"stdout":"","stderr":str(e)})()

def active(u):
    r=run(["systemctl","is-active",u],8); return r.returncode==0 and r.stdout.strip()=="active"

def load_json(path,dv):
    try:
        with open(path,encoding="utf-8") as f:return json.load(f)
    except Exception:return dv

def hw_limit():
    r=run([LIMIT,"--json"],8)
    try:return json.loads(r.stdout)
    except Exception:return {"max_channels":7,"profile":"未知设备","reason":"未检测到硬件限制","model":"","memory_mb":0,"cpu_cores":0}

def _preserve_identity(d):
    """空的 callsign / dmr_id 不许覆盖已有值。否则会把 channels.json 的身份清空，
    导致下一次 channel-setup.py apply 把 DMR 关掉（射频收到也不解码）。"""
    import re as _re
    old = load_json(PLAN, {}) or {}
    for key in ("callsign", "dmr_id"):
        newv = str(d.get(key) or "").strip()
        bad = (not newv) or newv.upper() in ("NOCALL", "NONE") or               (key == "dmr_id" and not _re.search(r"[1-9]", newv))
        if bad:
            keep = str(old.get(key) or "").strip()
            if not _re.search(r"[1-9]", keep) if key == "dmr_id" else not keep:
                # 再从 DMRGateway.ini 兜底（DMR 页面填过的 ID / 呼号）
                try:
                    g = ini("/etc/DMRGateway.ini")
                    keep = (g.get("General", "Id" if key == "dmr_id" else "Callsign",
                                   fallback="") or "").strip() or keep
                except Exception:
                    pass
            if keep:
                d[key] = keep
    return d


def normalize_plan(d):
    d=dict(d or {}); lim=int(hw_limit().get("max_channels") or 7)
    d["channels"]=max(1,min(lim,int(d.get("channels") or 1)))
    d["list"]=[x for x in (d.get("list") or []) if int(x.get("n",99))<=d["channels"]]
    d["hardware_max"]=lim
    d=_preserve_identity(d)
    return d

def plan(): return normalize_plan(load_json(PLAN,{"channels":1,"center_rx":434500000,"center_tx":439500000,"sample_rate":150000,"list":[]}))

def ini(path):
    c=configparser.RawConfigParser(); c.optionxform=str
    try:c.read(path,encoding="utf-8")
    except Exception:pass
    return c

def out(d,code=0): print(json.dumps(d,ensure_ascii=False)); return code

def channels():
    p=plan(); m=ini(MULTI); n=int(p.get("channels") or m.get("General","Channels",fallback=1))
    by={int(x.get("n",0)):x for x in p.get("list",[]) if str(x.get("n","")).isdigit()}
    rows=[]
    for i in range(1,n+1):
        x=by.get(i,{"n":i,"mode":"NONE","rx":0,"tx":0})
        rows.append({"n":i,"mode":x.get("mode") or "NONE","rx":int(x.get("rx") or 0),"tx":int(x.get("tx") or 0),
                     "mport":48200+i-1,"hport":3335+2*(i-1),
                     "iq":active("mmdvm-iq@%d.service"%i),"host":active("mmdvmhost@%d.service"%i),"lastq":""})
    hw=hw_limit()
    return out({"ok":True,"hardware_max":hw.get("max_channels"),"hardware":hw,"multi":{"active":active("mmdvm-multi.service"),"channels":n,
                "rate":m.get("Modem","SampleRate",fallback=p.get("sample_rate",300000)),
                "sdr":m.get("Modem","Type",fallback="sx"),
                "rx":m.get("Modem","RxFrequency",fallback=p.get("center_rx",0)),
                "tx":m.get("Modem","TxFrequency",fallback=p.get("center_tx",0)),
                "rxg":m.get("Modem","RxGain",fallback=None),"txg":m.get("Modem","TxGain",fallback=None)},
                "channels":rows,"services":[{"name":"MMDVM-Multi","active":active("mmdvm-multi.service")},
                {"name":"DMRGateway","active":active("dmrgateway.service")}]} )

def main():
    a=sys.argv[1:]; cmd=a[0] if a else "channels"
    if cmd=="channels": return channels()
    if cmd=="switch" and len(a)>=3:
        if int(a[1])>int(hw_limit().get("max_channels") or 7): return out({"ok":False,"error":hw_limit().get("reason","硬件通道数已达上限")},1)
        r=run(["sudo",CS,"set",a[1],a[2]],180); return out({"ok":r.returncode==0,"message":r.stdout.strip(),"error":r.stderr.strip()},0 if r.returncode==0 else 1)
    if cmd=="restart" and len(a)>=2:
        n=int(a[1]);
        if n>int(hw_limit().get("max_channels") or 7): return out({"ok":False,"error":hw_limit().get("reason","硬件通道数已达上限")},1)
        run(["systemctl","restart","mmdvm-iq@%d.service"%n],60); r=run(["systemctl","restart","mmdvmhost@%d.service"%n],60)
        return out({"ok":r.returncode==0,"error":r.stderr.strip()},0 if r.returncode==0 else 1)
    if cmd=="multi-restart":
        r=run(["systemctl","restart","mmdvm-multi.service"],80); return out({"ok":r.returncode==0,"error":r.stderr.strip()},0 if r.returncode==0 else 1)
    if cmd=="logs":
        ch=a[1] if len(a)>1 else "0"; svc=a[2] if len(a)>2 else "mmdvmhost"; lines=a[3] if len(a)>3 else "200"
        unit=(svc+"@%s.service"%ch) if ch not in ("","0") and svc in ("mmdvmhost","mmdvm-iq") else svc+".service"
        r=run(["journalctl","-u",unit,"-n",str(lines),"--no-pager"],30)
        return out({"ok":r.returncode==0,"text":r.stdout,"error":r.stderr},0 if r.returncode==0 else 1)
    if cmd=="plan":
        d=plan(); d["ok"]=True; d["hardware"]=hw_limit(); return out(d)
    if cmd in ("plan-save","plan-apply") and len(a)>=2:
        try:d=json.loads(a[1])
        except Exception as e:return out({"ok":False,"error":"参数错误: %s"%e},1)
        lim=int(hw_limit().get("max_channels") or 7); requested=int(d.get("channels") or 1)
        d=normalize_plan(d)
        os.makedirs(os.path.dirname(PLAN),exist_ok=True)
        with open(PLAN,"w",encoding="utf-8") as f:json.dump(d,f,ensure_ascii=False,indent=2)
        if cmd=="plan-apply":
            r=run(["sudo",CS,"apply",json.dumps(d,ensure_ascii=False)],240)
            return out({"ok":r.returncode==0,"message":r.stdout.strip(),"error":r.stderr.strip()},0 if r.returncode==0 else 1)
        msg="规划已保存"
        if requested>lim: msg="当前硬件最多支持 %d 路，已自动限制为 %d 路"%(lim,lim)
        return out({"ok":True,"message":msg,"hardware_max":lim,"channels":d.get("channels")})
    return out({"ok":False,"error":"未知操作"},1)

if __name__=="__main__":raise SystemExit(main())