#!/usr/bin/env python3
import configparser, os, sys
if len(sys.argv)!=6 or sys.argv[2]!="set":
    print("用法: ini-edit.py FILE set SECTION KEY VALUE",file=sys.stderr); raise SystemExit(2)
p,_,sec,key,val=sys.argv[1:]
c=configparser.RawConfigParser(); c.optionxform=str
try:c.read(p,encoding="utf-8")
except Exception:pass
if not c.has_section(sec):c.add_section(sec)
c.set(sec,key,val)
t=p+".tmp"
with open(t,"w",encoding="utf-8") as f:c.write(f)
os.replace(t,p)