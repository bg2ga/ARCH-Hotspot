#!/bin/bash
# ARCH 首次启动/定期数据库下载：DMR ID、各模式 hosts、反射器列表。
set -u
LOG=/var/log/arch-data-bootstrap.log
STATE=/var/lib/mmdvm/data-bootstrap.status
mkdir -p /var/lib/mmdvm /usr/local/etc/hosts /etc/mmdvm
exec >>"$LOG" 2>&1
echo "[$(date '+%F %T')] 开始更新 ARCH 数据库"
fail=0
/usr/local/bin/update-hosts.sh || fail=$((fail+1))
# 常规情况下 Pi-Star 已提供 DMR ID 和各模式主机文件；仅在缺失时走备用源。
if [ ! -s /usr/local/etc/DMRIds.dat ]; then
  /usr/local/bin/update-database.sh dmrid || fail=$((fail+1))
fi
need_hosts=0
for f in DMR_Hosts.txt YSFHosts.txt P25Hosts.txt NXDNHosts.txt DStar_Hosts.json; do
  [ -s "/usr/local/etc/hosts/$f" ] || need_hosts=1
done
if [ "$need_hosts" -eq 1 ]; then
  /usr/local/bin/update-database.sh hosts || fail=$((fail+1))
fi
/usr/local/libexec/hosts-build.py -v || fail=$((fail+1))
python3 - <<'PY'
import json
try:
    src='/usr/local/etc/hosts/DMR_Hosts.json'
    dst='/etc/mmdvm/bm-masters.json'
    data=json.load(open(src,encoding='utf-8'))
    out=[]
    for x in data if isinstance(data,list) else []:
        out.append({'id':str(x.get('id','')),'net':'BM','label':x.get('label') or x.get('host',''),
                    'host':x.get('host',''),'port':int(x.get('port') or 62031),
                    'pw':x.get('pw',''),'region':x.get('region',''),'country':x.get('country','')})
    json.dump(out,open(dst,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
except Exception:
    pass
PY
status=ok
[ "$fail" -eq 0 ] || status=partial
printf 'STATUS=%s\nUPDATED=%s\nFAIL=%s\n' "$status" "$(date '+%F %T')" "$fail" > "$STATE"
echo "[$(date '+%F %T')] 更新结束：$status（失败项 $fail）"
exit 0
