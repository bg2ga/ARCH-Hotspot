#!/usr/bin/env bash
set -euo pipefail
# ARCH network priority: wired > WiFi > USB/4G, with internet health failover.
# Keep the route stable when it is already correct.  The old implementation
# deleted and recreated the default route on every timer tick, which caused
# short audio dropouts on FMO links.
LOG=/var/log/arch-net-priority.log
STATE=/run/arch-net-priority.state
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

# Broadcom WiFi power-save can cause periodic SDIO stalls on Raspberry Pi.
if command -v iw >/dev/null 2>&1; then
  iw dev wlan0 set power_save off >/dev/null 2>&1 || true
fi

healthy(){
  local ifc="$1"
  ping -I "$ifc" -c 1 -W 2 223.5.5.5 >/dev/null 2>&1 && return 0
  ping -I "$ifc" -c 1 -W 2 114.114.114.114 >/dev/null 2>&1 && return 0
  curl -4 --interface "$ifc" -fsS --connect-timeout 3 --max-time 5 http://connectivitycheck.gstatic.com/generate_204 >/dev/null 2>&1 && return 0
  return 1
}

current_gw(){
  ip -4 route show default dev "$1" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}'
}

current_metric(){
  ip -4 route show default dev "$1" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="metric"){print $(i+1); exit}}'
}

n=0
while read -r ifc; do
  [ -n "$ifc" ] || continue
  case "$ifc" in lo) continue ;; esac
  ip4=$(ip -4 -o addr show dev "$ifc" scope global 2>/dev/null | awk '{print $4; exit}')
  gw=$(ip -4 route show default dev "$ifc" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}')
  [ -n "$ip4" ] && [ -n "$gw" ] || continue
  case "$ifc" in
    eth0) base=100 ;;
    wlan0) base=200 ;;
    *) n=$((n+1)); base=$((290 + n*10)) ;;
  esac
  if healthy "$ifc"; then metric=$base; state=online; else metric=$((9000 + base)); state=offline; fi
  cur_gw=$(current_gw "$ifc")
  cur_metric=$(current_metric "$ifc")
  # Only touch routing when the desired route is not already in place.
  if [ "$cur_gw" != "$gw" ] || [ "$cur_metric" != "$metric" ]; then
    ip -4 route replace default via "$gw" dev "$ifc" metric "$metric" >/dev/null 2>&1 || true
    # Remove only superseded routes for this interface, after the desired one exists.
    while read -r rline; do
      rm=$(printf '%s\n' "$rline" | awk '{for(i=1;i<=NF;i++) if($i=="metric"){print $(i+1); exit}}')
      if [ -n "$rm" ] && [ "$rm" != "$metric" ]; then
        ip -4 route del $rline >/dev/null 2>&1 || true
      fi
    done < <(ip -4 route show default dev "$ifc" 2>/dev/null)
  fi
  printf '%s|%s|%s|%s\n' "$ifc" "$metric" "$gw" "$state" >> "$TMP"
done < <(ip -4 -o addr show up scope global 2>/dev/null | awk '{print $2}' | sort -u)
sort -o "$TMP" "$TMP"
if [ ! -f "$STATE" ] || ! cmp -s "$TMP" "$STATE"; then
  { echo "[$(date '+%F %T')] network priority update"; cat "$TMP"; } >> "$LOG"
  cp "$TMP" "$STATE"
fi
