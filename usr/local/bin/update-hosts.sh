#!/bin/bash
# ARCH / Pi-Star 主机库与 ID 数据库更新器：ARCH 镜像优先，Pi-Star 仅作回退。
set -Eeuo pipefail
MIRROR="${ARCH_MIRROR_BASE:-https://arch.hljrsa.cn/downloads/arch-hotspot/data}"
FALLBACK="${ARCH_PISTAR_BASE:-https://www.pistar.uk/downloads}"
DEST="/usr/local/etc/hosts"
ETC="/usr/local/etc"
mkdir -p "$DEST" "$ETC"
cd "$DEST"

fetch() {
    local rel="$1" dst="$2" min="${3:-100}" base url sz i
    for base in "$MIRROR" "$FALLBACK"; do
        url="${base%/}/$rel"
        for i in 1 2 3; do
            if curl -fsSL --retry 1 --connect-timeout 15 --max-time 180 -A 'ARCH-Hotspot/1.0' "$url" -o "$dst.tmp" 2>/dev/null; then
                sz=$(stat -c%s "$dst.tmp" 2>/dev/null || echo 0)
                if [ "$sz" -ge "$min" ]; then mv -f "$dst.tmp" "$dst"; return 0; fi
            fi
            rm -f "$dst.tmp"; sleep 2
        done
    done
    echo "  下载失败: $rel"
    return 1
}

OK=0; FAIL=0
run(){ if fetch "$1" "$2" "${3:-100}"; then OK=$((OK+1)); else FAIL=$((FAIL+1)); fi; }

run DMRIds.dat           DMRIds.dat           100000
run DMR_Hosts.txt        DMR_Hosts.txt
run DMR_Hosts.json       DMR_Hosts.json
run XLX_Hosts.txt        XLX_Hosts.txt
run DPlus_Hosts.txt      DPlus_Hosts.txt
run DExtra_Hosts.txt     DExtra_Hosts.txt
run DCS_Hosts.txt        DCS_Hosts.txt
run CCS_Hosts.txt        CCS_Hosts.txt
run FCS_Hosts.txt        FCS_Hosts.txt
run FCSHosts.json        FCSHosts.json
run YSF_Hosts.txt        YSF_Hosts.txt
run YSFHosts.json        YSFHosts.json
run NXDN_Hosts.txt       NXDN_Hosts.txt
run NXDNHosts.json       NXDNHosts.json
run NXDN.csv             NXDN.csv
run P25_Hosts.txt        P25_Hosts.txt
run M17_Hosts.txt        M17_Hosts.txt
run APRS_Hosts.txt       APRS_Hosts.txt
run DStar_Hosts.json     DStar_Hosts.json
run DSTAR_Reflectors.json DSTAR_Reflectors.json

# 生成/校正 Web 控制台和网关共用的 JSON。
python3 /usr/local/libexec/hosts-build.py -v || FAIL=$((FAIL+1))

# DMR ID 数据库的运行时路径。
if [ -s "$DEST/DMRIds.dat" ]; then cp -f "$DEST/DMRIds.dat" "$ETC/DMRIds.dat"; fi
if [ -s "$ETC/DMRIds.dat" ]; then head -2 "$ETC/DMRIds.dat" > "$ETC/DMRIds-min.dat" 2>/dev/null || true; fi

# 网关实际读取的 JSON/文本路径。
for f in DMR_Hosts.json YSFHosts.json NXDNHosts.json FCSHosts.json DSTAR_Reflectors.json hosts-index.json; do
    [ -s "$DEST/$f" ] && cp -f "$DEST/$f" "$ETC/$f"
done
for f in NXDNHosts.txt; do
    [ -s "$DEST/$f" ] && cp -f "$DEST/$f" "$ETC/$f"
done
[ -s "$DEST/NXDN.csv" ] && cp -f "$DEST/NXDN.csv" "$ETC/NXDN.csv"
[ -s "$DEST/DStar_Hosts.json" ] && cp -f "$DEST/DStar_Hosts.json" "$ETC/DStar_Hosts.json"

rm -f "$DEST"/*.tmp 2>/dev/null || true
chmod 644 "$DEST"/* 2>/dev/null || true
chmod 644 "$ETC"/*Hosts* "$ETC"/DMRIds*.dat "$ETC"/NXDN.csv "$ETC"/DSTAR_Reflectors.json 2>/dev/null || true
chown -R mmdvm-web:mmdvm-web "$DEST" 2>/dev/null || true
echo "ARCH 镜像更新完成：成功 $OK / 失败 $FAIL"
exit 0
