#!/bin/bash
# ARCH database updater: our Cloudflare/ARCH mirror is authoritative.
set -Eeuo pipefail
TYPE="${1:-dmrid}"
MIRROR="${ARCH_MIRROR_BASE:-https://arch.hljrsa.cn/downloads/arch-hotspot/data}"
PISTAR="${ARCH_PISTAR_BASE:-https://www.pistar.uk/downloads}"
LOCK_HELD=0
if [ "${MMDVM_INTERNAL:-0}" != "1" ]; then
  exec 9>/run/mmdvm-control.lock
  flock -w 120 9 || { echo "无法获取控制锁"; exit 1; }
  LOCK_HELD=1
fi
trap '[ "$LOCK_HELD" -eq 1 ] && flock -u 9 2>/dev/null || true' EXIT

validate_download() {
  local kind="$1" file="$2"
  case "$kind" in
    nonempty) [ -s "$file" ] ;;
    json) python3 -m json.tool "$file" >/dev/null 2>&1 ;;
    dmrids) grep -Eq '^[[:alnum:]]' "$file" ;;
    p25) grep -Eq '^[0-9]+[[:space:]]' "$file" ;;
    line) grep -q '^' "$file" ;;
    "") return 0 ;;
    *) return 1 ;;
  esac
}

atomic_url() {
  local url="$1" dst="$2" validator="${3:-}" tmp="${2}.new"
  curl -fsSL --retry 2 --connect-timeout 15 --max-time 180 -A 'ARCH-Hotspot/1.0' "$url" -o "$tmp" 2>/dev/null || { rm -f "$tmp"; return 1; }
  [ -s "$tmp" ] || { rm -f "$tmp"; return 1; }
  validate_download "$validator" "$tmp" || { rm -f "$tmp"; return 1; }
  mv -f "$tmp" "$dst"
}

from_mirror_or_upstream() {
  local rel="$1" dst="$2" validator="${3:-}" url
  for url in "$MIRROR/$rel" "$PISTAR/$rel"; do
    atomic_url "$url" "$dst" "$validator" && return 0
  done
  return 1
}

ensure_dirs() { mkdir -p /usr/local/etc /usr/local/etc/hosts /usr/local/etc/private; }

case "$TYPE" in
 dmrid)
   ensure_dirs
   if from_mirror_or_upstream DMRIds.dat /usr/local/etc/DMRIds.dat "nonempty"; then
      cp -f /usr/local/etc/DMRIds.dat /usr/local/etc/hosts/DMRIds.dat
      head -2 /usr/local/etc/DMRIds.dat > /usr/local/etc/DMRIds-min.dat 2>/dev/null || true
      echo "DMR ID 更新成功（ARCH 镜像）"; exit 0
   fi
   if [ -x /usr/local/bin/DMRIDUpdate.sh ] && /usr/local/bin/DMRIDUpdate.sh && [ -s /usr/local/etc/DMRIds.dat ]; then
      cp -f /usr/local/etc/DMRIds.dat /usr/local/etc/hosts/DMRIds.dat
      echo "DMR ID 更新成功"; exit 0
   fi
   echo "DMR ID 更新失败"; exit 1
   ;;
 ysfhosts)
   ensure_dirs
   if from_mirror_or_upstream YSFHosts.json /usr/local/etc/YSFHosts.json "json"; then
      cp -f /usr/local/etc/YSFHosts.json /usr/local/etc/hosts/YSFHosts.json
      echo "YSF Hosts 更新成功（ARCH 镜像）"; exit 0
   fi
   echo "YSF Hosts 更新失败"; exit 1
   ;;
 p25hosts)
   ensure_dirs
   if from_mirror_or_upstream P25_Hosts.txt /usr/local/etc/hosts/P25_Hosts.txt "p25"; then
      python3 /usr/local/libexec/hosts-build.py --p25-only >/dev/null
      echo "P25 Hosts 更新成功（ARCH 镜像）"; exit 0
   fi
   echo "P25 Hosts 更新失败"; exit 1
   ;;
 nxdnhosts)
   ensure_dirs
   ok=0
   from_mirror_or_upstream NXDNHosts.json /usr/local/etc/NXDNHosts.json "json" && ok=1
   from_mirror_or_upstream NXDN.csv /usr/local/etc/NXDN.csv "line" && ok=1
   cp -f /usr/local/etc/NXDNHosts.json /usr/local/etc/hosts/NXDNHosts.json 2>/dev/null || true
   cp -f /usr/local/etc/NXDN.csv /usr/local/etc/hosts/NXDN.csv 2>/dev/null || true
   [ "$ok" -eq 1 ] && { echo "NXDN Hosts/ID 更新成功（ARCH 镜像）"; exit 0; }
   echo "NXDN Hosts/ID 更新失败"; exit 1
   ;;
 dstarhosts)
   ensure_dirs
   from_mirror_or_upstream DSTAR_Reflectors.json /usr/local/etc/hosts/DSTAR_Reflectors.json "json" || true
   cp -f /usr/local/etc/hosts/DSTAR_Reflectors.json /usr/local/etc/DSTAR_Reflectors.json 2>/dev/null || true
   for f in DCS_Hosts.txt DExtra_Hosts.txt DPlus_Hosts.txt XLX_Hosts.txt CCS_Hosts.txt FCS_Hosts.txt DStar_Hosts.json; do
      from_mirror_or_upstream "$f" "/usr/local/etc/hosts/$f" "" || true
   done
   echo "D-STAR Hosts 更新完成（ARCH 镜像）"
   ;;
 dmrhosts)
   ensure_dirs
   ok=0
   from_mirror_or_upstream DMR_Hosts.txt /usr/local/etc/hosts/DMR_Hosts.txt "" && ok=1
   from_mirror_or_upstream DMR_Hosts.json /usr/local/etc/hosts/DMR_Hosts.json "json" || true
   python3 /usr/local/libexec/hosts-build.py >/dev/null 2>&1 || true
   if [ "$ok" -eq 1 ]; then echo "DMR Hosts 更新成功（ARCH 镜像）"; exit 0; fi
   echo "DMR Hosts 更新失败"; exit 1
   ;;
 hosts)
   fail=0
   MMDVM_INTERNAL=1 "$0" dmrhosts || fail=$((fail+1))
   MMDVM_INTERNAL=1 "$0" ysfhosts || fail=$((fail+1))
   MMDVM_INTERNAL=1 "$0" p25hosts || fail=$((fail+1))
   MMDVM_INTERNAL=1 "$0" nxdnhosts || fail=$((fail+1))
   MMDVM_INTERNAL=1 "$0" dstarhosts || fail=$((fail+1))
   [ "$fail" -eq 0 ] && echo "Hosts 全量更新成功（ARCH 镜像）" || { echo "Hosts 有 $fail 项更新失败"; exit 1; }
   ;;
 *) echo "用法: $0 {dmrid|dmrhosts|ysfhosts|p25hosts|nxdnhosts|dstarhosts|hosts}"; exit 2;;
esac
