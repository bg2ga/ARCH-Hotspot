#!/usr/bin/env python3
import glob, json, os, re
CFG="/opt/fmo-bridge/config.json"
TONE="/etc/mmdvm/fm-tailtone.json"

def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save(path, data):
    tmp=path+".tmp"
    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
        f.write("\n")
    os.replace(tmp,path)

cfg=load(CFG,{})
cfg["app_relay_enabled"]=False
cfg.setdefault("voice",{})["codec"]="radpcm"
fm=cfg.setdefault("fm",{})
dsp=fm.setdefault("dsp",{})
dsp["enable"]=True
dsp["agc"]=False
tone=load(TONE,{"enable":True,"freq":1000,"ms":200,"delay_ms":0,"gain":1.0})
try:
    save(CFG,cfg)
except Exception as e:
    print("migrate config warning:",e)
try:
    save(TONE,tone)
    os.chmod(TONE,0o644)
except Exception as e:
    print("migrate tone warning:",e)

enabled=bool(tone.get("enable",True))
freq=int(float(tone.get("freq",1000)))
ms=max(40,min(2000,int(float(tone.get("ms",200)))))
delay=max(0,min(5000,int(float(tone.get("delay_ms",120)))))
gain=max(0.0,min(1.0,float(tone.get("gain",0.7))))
ack={"RFAck":"K" if enabled else "N","AckFrequency":str(freq),
     "AckSpeed":str(max(4,min(200,ms//10))),"AckMinTime":"0",
     "AckDelay":str(delay),"AckLevel":str(gain*100.0)}
for path in sorted(glob.glob("/etc/MMDVM-Host-*.ini")):
    try:
        lines=open(path,encoding="utf-8",errors="replace").readlines()
        start=end=None
        for i,line in enumerate(lines):
            s=line.strip()
            if s == "[FM]":
                start=i
            elif start is not None and s.startswith("[") and s.endswith("]"):
                end=i; break
        if start is None:
            continue
        if end is None:
            end=len(lines)
        out=[]; seen=set()
        for line in lines[start+1:end]:
            m=re.match(r"\s*([A-Za-z0-9_]+)\s*=", line)
            if m and m.group(1) in ack:
                k=m.group(1); out.append("%s=%s\n" % (k,ack[k])); seen.add(k)
            else:
                out.append(line if line.endswith("\n") else line+"\n")
        for k,v in ack.items():
            if k not in seen:
                out.append("%s=%s\n" % (k,v))
        tmp=path+".arch.tmp"
        open(tmp,"w",encoding="utf-8").writelines(lines[:start+1]+out+lines[end:])
        os.replace(tmp,path)
    except Exception as e:
        print("migrate host warning %s: %s" % (path,e))
print("arch fmo migrate 1.0.30 done")
