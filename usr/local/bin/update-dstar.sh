#!/bin/bash
# ARCH D-STAR host updater: fetch the prebuilt ARCH mirror files.
set -Eeuo pipefail
HOSTS=/usr/local/etc/hosts
BASE="${ARCH_MIRROR_BASE:-https://arch.hljrsa.cn/downloads/arch-hotspot/data}"
mkdir -p "$HOSTS"
echo "=== Updating D-STAR host lists ==="
for f in DCS_Hosts.txt DExtra_Hosts.txt DPlus_Hosts.txt XLX_Hosts.txt CCS_Hosts.txt FCS_Hosts.txt DStar_Hosts.json DSTAR_Reflectors.json; do
  tmp="$HOSTS/$f.new"
  if curl -fsSL --retry 2 --connect-timeout 15 --max-time 180 -A 'ARCH-Hotspot/1.0' "$BASE/$f" -o "$tmp" 2>/dev/null; then
      [ -s "$tmp" ] && mv -f "$tmp" "$HOSTS/$f" || rm -f "$tmp"
  else
      rm -f "$tmp"
      echo "  下载失败: $f"
  fi
done
[ -s "$HOSTS/DSTAR_Reflectors.json" ] && cp -f "$HOSTS/DSTAR_Reflectors.json" /usr/local/etc/DSTAR_Reflectors.json
chmod 644 "$HOSTS"/DCS_Hosts.txt "$HOSTS"/DExtra_Hosts.txt "$HOSTS"/DPlus_Hosts.txt "$HOSTS"/XLX_Hosts.txt "$HOSTS"/CCS_Hosts.txt "$HOSTS"/FCS_Hosts.txt "$HOSTS"/DSTAR_Reflectors.json 2>/dev/null || true
echo "=== D-STAR hosts update complete ==="
