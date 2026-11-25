#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, re, sys
ROOT="/usr/local/etc"
H=os.path.join(ROOT,"hosts")
os.makedirs(H,exist_ok=True)

def lines(name):
    for p in (os.path.join(H,name), os.path.join(ROOT,name)):
        try:
            with open(p,encoding="utf-8",errors="ignore") as f:
                return [x.rstrip("\n") for x in f]
        except OSError:
            pass
    return []

def write(name,value):
    for d in (H,ROOT):
        with open(os.path.join(d,name),"w",encoding="utf-8") as f:
            json.dump(value,f,ensure_ascii=False,indent=2)

def write_path(path,value):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    tmp=path+".tmp"
    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(value,f,ensure_ascii=False,indent=2)
        f.write("\n")
    os.replace(tmp,path)

# ---- ARCH 自建服务器（始终置顶，统一注释“二区多模融合”）----
_ARCH_DMR = {"id": "46002", "host": "srv2.hljrsa.cn", "port": 62030, "pw": "",
             "net": "DMR", "label": "ARCH_46002_China",
             "region": "46002 二区多模融合", "country": "CN"}
_ARCH_YSF = {"designator": "46999", "country": "CN", "name": "CN-46002",
             "use_xx_prefix": False, "user_count": "000",
             "description": "二区多模融合 / 哈尔滨",
             "port": 42000, "ipv4": "ysf.hljrsa.cn", "ipv6": None}
_ARCH_P25 = {"designator": 46002, "name": "46002 二区多模融合",
             "port": 41000, "ipv4": "39.96.117.224", "ipv6": None}
_ARCH_NXDN = {"designator": 46002, "name": "46002 二区多模融合",
              "port": 41400, "ipv4": "39.96.117.224", "ipv6": None}
_ARCH_DSTAR = {
    "DCS": [{"name": "DCSHLJ 46002二区多模融合", "ip": "39.96.117.224"}],
    "XRF": [{"name": "XRFHLJ 46002二区多模融合", "ip": "39.96.117.224"}],
    "REF": [{"name": "REFHLJ 46002二区多模融合", "ip": "39.96.117.224"}],
    "XLX": [{"name": "XLXHLJ 46002二区多模融合", "ip": "39.96.117.224"}],
}

_DMR_CMAP = {
    "china": "CN", "chinese": "CN", "hongkong": "HK", "taiwan": "TW",
    "japan": "JP", "korea": "KR", "singapore": "SG", "malaysia": "MY", "thailand": "TH",
    "indonesia": "ID", "philippines": "PH", "india": "IN", "vietnam": "VN",
    "usa": "US", "united": "US", "canada": "CA", "mexico": "MX", "brazil": "BR",
    "argentina": "AR", "chile": "CL", "colombia": "CO", "peru": "PE",
    "uk": "GB", "england": "GB", "scotland": "GB", "wales": "GB", "ireland": "IE",
    "germany": "DE", "france": "FR", "spain": "ES", "italy": "IT", "portugal": "PT",
    "netherlands": "NL", "belgium": "BE", "switzerland": "CH", "austria": "AT",
    "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI", "iceland": "IS",
    "poland": "PL", "czech": "CZ", "slovakia": "SK", "hungary": "HU", "romania": "RO",
    "bulgaria": "BG", "greece": "GR", "croatia": "HR", "serbia": "RS", "slovenia": "SI",
    "turkey": "TR", "russia": "RU", "ukraine": "UA", "belarus": "BY",
    "australia": "AU", "newzealand": "NZ", "southafrica": "ZA", "egypt": "EG",
    "israel": "IL", "saudiarabia": "SA", "uae": "AE", "iran": "IR", "iraq": "IQ",
    "europe": "EU", "world": "WW", "global": "WW", "asia": "AS", "africa": "AF",
}
_DMR_ISO2 = set(_DMR_CMAP.values()) | {"HK", "MO", "PR", "GU", "LU", "MT", "CY", "EE",
                                        "LV", "LT", "MD", "AL", "MK", "BA", "ME", "GE",
                                        "AM", "AZ", "KZ", "UZ"}
_DMR_PIN_TOP = ("4602", "4501")
_DMR_TIER_CN = {"CN"}
_DMR_TIER_ASIA = {"HK", "TW", "MO", "JP", "SG", "MY", "TH", "ID", "PH", "VN", "IN"}

def _dmr_country(label):
    for tok in re.split(r"[^A-Za-z]+", str(label or "")):
        if not tok:
            continue
        up = tok.upper()
        if len(up) == 2 and up in _DMR_ISO2:
            return up
        low = tok.lower()
        if low in _DMR_CMAP:
            return _DMR_CMAP[low]
    return ""

def _dmr_region(label):
    parts = str(label or "").split("_")
    return " ".join(parts[1:]) if len(parts) > 1 else str(label or "")

def _dmr_tier(country):
    if country in _DMR_TIER_CN:
        return 0
    if country in _DMR_TIER_ASIA:
        return 1
    return 2

def dmr():
    rows = []
    for line in lines("DMR_Hosts.txt"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = [x for x in re.split(r"[\t ]+", line) if x]
        if len(p) < 4:
            continue
        label, rid, host, pw = p[:4]
        port = p[4] if len(p) > 4 else "62031"
        if host.startswith("127.0.0.") or host in ("localhost", "0.0.0.0"):
            continue
        if not re.match(r"^[A-Za-z0-9_.\-]+$", host):
            continue
        net = label.split("_")[0].upper()
        rows.append({
            "id": "" if rid in ("0000", "0") else str(rid),
            "host": host,
            "port": int(port) if str(port).isdigit() else 62031,
            "pw": "" if str(pw).lower() in ("none", "") else pw,
            "net": net,
            "label": label,
            "region": _dmr_region(label),
            "country": _dmr_country(label),
        })
    for i, row in enumerate(rows):
        row["_i"] = i
    def rank(row):
        net = row["net"]
        if net == "BM":
            group = _DMR_PIN_TOP.index(row["id"]) if row["id"] in _DMR_PIN_TOP else len(_DMR_PIN_TOP)
            tier = 0
        else:
            group = len(_DMR_PIN_TOP) + 1
            tier = _dmr_tier(row["country"])
        return (group, tier, row["_i"])
    rows.sort(key=rank)
    for row in rows:
        row.pop("_i", None)
    # ★ ARCH 自建服务器（交换机）始终置顶，避免被上游排序挤到后面
    rows = [r for r in rows
            if not (str(r.get("host")) == _ARCH_DMR["host"]
                    and int(r.get("port") or 0) == int(_ARCH_DMR["port"]))]
    rows.insert(0, dict(_ARCH_DMR))
    return rows
def reflectors(name):
    rows=[]
    for line in lines(name):
        line=line.strip()
        if not line or line.startswith("#"): continue
        p=[x for x in re.split(r"[\t ]+",line) if x]
        if len(p)>=3:
            try: rows.append({"designator":int(p[0]),"port":int(p[2]),"ipv4":p[1],"ipv6":None})
            except ValueError: pass
    return {"reflectors":rows}

def ysf():
    rows=[]
    for line in lines("YSF_Hosts.txt"):
        line=line.strip()
        if not line or line.startswith("#"): continue
        p=[x.strip() for x in line.split(";")]
        if len(p)<5 or not p[0] or not p[3]: continue
        try: port=int(p[4] or 42000)
        except ValueError: continue
        name=p[1]
        if "-" in name: country,short=name.split("-",1); use_xx=False
        else: country,short,use_xx="",name,True
        rows.append({"designator":p[0],"country":country,"name":short,"use_xx_prefix":use_xx,
                     "user_count":p[5] if len(p)>5 and p[5] else "000","description":p[2] or short,
                     "port":port,"ipv4":p[3],"ipv6":None})
    # ★ ARCH 自建 YSF 房间置顶，并去掉上游的重复条目
    rows = [r for r in rows
            if not (str(r.get("designator")) == str(_ARCH_YSF["designator"])
                    or str(r.get("ipv4")) == _ARCH_YSF["ipv4"])]
    rows.insert(0, dict(_ARCH_YSF))
    return {"reflectors":rows}

def fcs():
    rows=[]
    for line in lines("FCS_Hosts.txt"):
        line=line.strip()
        if not line or line.startswith("#"): continue
        p=line.split(";")
        if p and p[0]: rows.append({"id":p[0],"name":p[1] if len(p)>1 else p[0],
                                     "desc":p[2] if len(p)>2 else (p[1] if len(p)>1 else p[0])})
    return rows

def dstar():
    groups={"DCS":[],"XRF":[],"REF":[],"XLX":[],"CCS":[]}
    try:
        data=json.load(open(os.path.join(H,"DStar_Hosts.json"),encoding="utf-8"))
        for x in data.get("reflectors",[]):
            typ=str(x.get("reflector_type","")).upper()
            if typ in groups and (x.get("ipv4") or x.get("ipv6")):
                groups[typ].append({"name":x.get("name",""),"ip":x.get("ipv4") or x.get("ipv6")})
    except Exception: pass
    groups["FCS"]=[{"name":x.get("id",""),"ip":x.get("desc","")} for x in fcs()]
    # ★ ARCH 自建 D-STAR/XLX 条目置顶，并补上 46002 注释
    for _k, _items in _ARCH_DSTAR.items():
        for _it in reversed(_items):
            if not any(str(x.get("name", "")).upper() == _it["name"].upper()
                       for x in groups.get(_k, [])):
                groups.setdefault(_k, []).insert(0, dict(_it))
    return groups

def build_p25_outputs():
    p=reflectors("P25_Hosts.txt")
    aliases=[dict(_ARCH_P25)]
    ids={"46002","46020"}
    refs=[x for x in p.get("reflectors",[]) if str(x.get("designator","")) not in ids]
    full={"reflectors":aliases+refs}
    gateway={"reflectors":aliases}
    # Web UI uses the full public list.
    write_path(os.path.join(H,"P25Hosts.json"),full)
    # P25Gateway only needs the ARCH static reflector, avoiding slow DNS scans.
    write_path(os.path.join(ROOT,"P25Hosts.json"),gateway)
    os.makedirs(os.path.join(ROOT,"private"),exist_ok=True)
    write_path(os.path.join(ROOT,"private","P25Hosts.json"),gateway)
    return full

def main():
    if "--p25-only" in sys.argv:
        p=build_p25_outputs()
        print("P25 主机库已重建：%d 条（含 ARCH 46002）" % len(p.get("reflectors",[])))
        return
    dm=dmr(); y=ysf(); p=build_p25_outputs(); n=reflectors("NXDN_Hosts.txt")
    _nx=[r for r in n.get("reflectors",[])
         if not (str(r.get("designator"))==str(_ARCH_NXDN["designator"])
                 or str(r.get("ipv4"))==_ARCH_NXDN["ipv4"])]
    _nx.insert(0,dict(_ARCH_NXDN)); n={"reflectors":_nx}
    write("DMR_Hosts.json",dm); write("YSFHosts.json",y)
    write("NXDNHosts.json",n); write("FCSHosts.json",fcs()); write("DSTAR_Reflectors.json",dstar())
    idx={"built":"","counts":{"DMR":len(dm),"YSF":len(y.get("reflectors",[])),
                               "P25":len(p.get("reflectors",[])),"NXDN":len(n.get("reflectors",[]))}}
    write("hosts-index.json",idx)
    print("主机库已重建：DMR %d，YSF %d，P25 %d，NXDN %d"%(idx["counts"]["DMR"],idx["counts"]["YSF"],idx["counts"]["P25"],idx["counts"]["NXDN"]))

if __name__=="__main__": main()
# [FIX 1.0.102] P25 hosts 后处理（单一来源，install 与 arch-data-bootstrap 都会走到这里）：
# hosts-build.py 原先把 /usr/local/etc/P25Hosts.json 写成旧格式（只有 1 条、name 是中文标签），
# 而本机这版 P25Gateway 把 JSON 的 name 字段当作【逐条 DNS 解析】的地址 → Unable to resolve。
# 这里重写为：保留全部【地址是 IP】的反射器（供用户在网页里自由选择），
# 并把默认要挂的 46002 固定写成 IP（无需 DNS）。默认只挂 46002 —— 挂几个由用户自己定。
def _arch_write_p25_hosts():
    import io as _io, json as _json, re as _re
    rows = {}
    for _p in ('/usr/local/etc/hosts/P25_Hosts.txt', '/usr/local/etc/hosts/P25Hosts.txt'):
        try:
            _f = _io.open(_p, encoding='utf-8', errors='replace')
        except Exception:
            continue
        for _ln in _f:
            _s = _ln.strip()
            if not _s or _s.startswith('#'):
                continue
            _a = _s.split()
            if len(_a) < 3 or not _a[0].isdigit():
                continue
            if not _re.match(r'^[0-9]+(\.[0-9]+){3}$', _a[1]):
                continue
            try:
                _port = int(_a[2])
            except Exception:
                continue
            rows[int(_a[0])] = {'designator': int(_a[0]), 'name': _a[1], 'port': _port, 'ipv4': _a[1], 'ipv6': None}
        break
    rows[46002] = {'designator': 46002, 'name': '39.96.117.224', 'port': 41000, 'ipv4': '39.96.117.224', 'ipv6': None}
    try:
        _io.open('/usr/local/etc/private/P25Hosts.json', 'w', encoding='utf-8').write(_json.dumps({'reflectors': []}, indent=2))
    except Exception:
        pass
    _io.open('/usr/local/etc/P25Hosts.json', 'w', encoding='utf-8').write(
        _json.dumps({'reflectors': [rows[_k] for _k in sorted(rows)]}, ensure_ascii=False, indent=2))


try:
    _arch_write_p25_hosts()
except Exception:
    pass
