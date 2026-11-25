#!/bin/bash
# [FIX 1.0.115] ARCH 应急热点（Wi-Fi AP）+ 无网常开 + 短暂暂停扫描回连
# 规则：
#   1) 有线、WiFi、4G 全都没连接 —— 10 秒 -> 开热点
#   2) 热点无密码，客户端可进入配置页，但不允许通过设备任何上网口访问互联网
#   3) 无任何网络时热点一直在线，不因空闲自动关闭
#   4) 任意正常网络恢复 -> 关闭热点，设备回连原 WiFi
#   5) 无客户端连接时，每 60 秒短暂暂停 AP，扫描并尝试回连已保存的 WiFi
set -u
AP_IF=uap0
AP_CON=arch-ap
AP_SSID='ARCH多模网关'
AP_IP=10.42.0.1
RECONNECT_SECS=180
AP_GRACE_SECS=120
STATE=/run/arch-ap-fallback.state
OPENED=/run/arch-ap-opened.since
RECONNECT_STAMP=/run/arch-ap-reconnect.stamp
AP_START_STAMP=/run/arch-ap-start.stamp
LOG=/var/log/arch-ap-fallback.log
DOWN_SINCE=/run/arch-ap-nouplink.since
BUILTIN_VID="0424 9514 0bda"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
is_builtin() {
  local vid; vid=$(cat "/sys/class/net/$1/device/../idVendor" 2>/dev/null || echo "")
  [ -z "$vid" ] && return 0
  for b in $BUILTIN_VID; do [ "$vid" = "$b" ] && return 0; done
  return 1
}
ap_clients() { local n; n=$(iw dev "$AP_IF" station dump 2>/dev/null | grep -c '^Station' || true); case "$n" in ''|*[!0-9]*) n=0 ;; esac; echo "$n"; }
ensure_dns_conf() {
  install -d -m 0755 /etc/NetworkManager/dnsmasq-shared.d 2>/dev/null || true
  printf 'address=/#/%s\ndhcp-option=114,http://%s/\n' "$AP_IP" "$AP_IP" > /etc/NetworkManager/dnsmasq-shared.d/arch-ap.conf 2>/dev/null || true
}
ensure_ap_profile() {
  if ! nmcli -t -f NAME connection show 2>/dev/null | grep -qx "$AP_CON"; then
    nmcli connection add type wifi ifname "$AP_IF" con-name "$AP_CON" ssid "$AP_SSID" autoconnect no save yes >/dev/null 2>&1 || true
  fi
  # [FIX 1.0.135] the old single modify failed entirely because
  # "ipv4.shared-dhcp-range 10.42.0.10,10.42.0.200,12h" is invalid for NM
  # (it takes START,END only) -> mode never became "ap" -> the fallback AP
  # could never start.  Apply each property separately, and let NM pick its
  # default shared range instead of pinning one.
  nmcli connection modify "$AP_CON" 802-11-wireless.mode ap >/dev/null 2>&1 || true
  nmcli connection modify "$AP_CON" 802-11-wireless.band bg >/dev/null 2>&1 || true
  nmcli connection modify "$AP_CON" 802-11-wireless.channel 6 >/dev/null 2>&1 || true
  nmcli connection modify "$AP_CON" 802-11-wireless.powersave 2 >/dev/null 2>&1 || true
  nmcli connection modify "$AP_CON" ipv4.method shared >/dev/null 2>&1 || true
  nmcli connection modify "$AP_CON" ipv6.method disabled >/dev/null 2>&1 || true
  nmcli connection modify "$AP_CON" connection.autoconnect no >/dev/null 2>&1 || true
}
ensure_uap0() {
  command -v iw >/dev/null 2>&1 || { log "缺少 iw，无法创建 $AP_IF"; return 1; }
  command -v rfkill >/dev/null 2>&1 && rfkill unblock wifi >/dev/null 2>&1 || true
  nmcli radio wifi on >/dev/null 2>&1 || true
  nmcli device set wlan0 managed yes >/dev/null 2>&1 || true
  if ! iw dev "$AP_IF" info >/dev/null 2>&1; then
    ip link set wlan0 down 2>/dev/null || true
    iw dev wlan0 interface add "$AP_IF" type __ap 2>/dev/null || true
    ip link set wlan0 up 2>/dev/null || true
  fi
  iw dev "$AP_IF" info >/dev/null 2>&1
}
policy_on() {
  ensure_dns_conf
  command -v iptables >/dev/null 2>&1 || return 0
  while iptables -D FORWARD -i "$AP_IF" -j REJECT 2>/dev/null; do :; done
  iptables -I FORWARD 1 -i "$AP_IF" -j REJECT 2>/dev/null || true
}
policy_off() {
  command -v iptables >/dev/null 2>&1 || return 0
  while iptables -D FORWARD -i "$AP_IF" -j REJECT 2>/dev/null; do :; done
}
start_ap() {
  ensure_dns_conf
  ensure_ap_profile
  ensure_uap0 || return 1
  ip link set "$AP_IF" up >/dev/null 2>&1 || true
  nmcli device set "$AP_IF" managed yes >/dev/null 2>&1 || true
  nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$AP_CON" || \
    nmcli connection up "$AP_CON" >/dev/null 2>&1 || true
  if nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$AP_CON"; then
    date +%s > "$AP_START_STAMP"
    return 0
  fi
  return 1
}
close_ap() {
  nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$AP_CON" && nmcli connection down "$AP_CON" >/dev/null 2>&1 || true
  iw dev "$AP_IF" info >/dev/null 2>&1 && iw dev "$AP_IF" del >/dev/null 2>&1 || true
  policy_off
  rm -f "$OPENED" "$RECONNECT_STAMP" "$AP_START_STAMP"
  echo 0 > "$STATE"
}
try_upstream_pause() {
  local now last n saved cn ac typ
  now=$(date +%s); last=$(cat "$RECONNECT_STAMP" 2>/dev/null || echo 0)
  [ $((now - last)) -ge "$RECONNECT_SECS" ] || return 0
  echo "$now" > "$RECONNECT_STAMP"
  local started
  started=$(cat "$AP_START_STAMP" 2>/dev/null || echo 0)
  [ $((now - started)) -ge "$AP_GRACE_SECS" ] || return 0
  n=$(ap_clients)
  if [ "$n" -gt 0 ]; then
    log "AP 有 $n 个客户端，跳过本次暂停扫描，保持连接稳定"
    return 0
  fi
  # 再观察一次，避免客户端正在关联/DHCP 时被误判为无客户端。
  sleep 3
  n=$(ap_clients)
  if [ "$n" -gt 0 ]; then
    log "AP 客户端正在连接（$n），跳过本次暂停扫描"
    return 0
  fi
  saved=0
  while IFS=: read -r cn ac typ; do
    [ "$cn" = "$AP_CON" ] && continue
    [ "$typ" = "802-11-wireless" ] || continue
    if [ "$ac" != "yes" ]; then
      nmcli connection modify "$cn" connection.autoconnect yes >/dev/null 2>&1 || true
      log "已为保存的 WiFi 打开自动连接: $cn"
    fi
    saved=1
  done < <(nmcli -t -f NAME,AUTOCONNECT,TYPE connection show 2>/dev/null || true)
  [ "$saved" = "1" ] || return 0
  log "AP 无客户端，短暂暂停 AP，扫描并尝试回连已保存的 WiFi"
  nmcli connection down "$AP_CON" >/dev/null 2>&1 || true
  iw dev "$AP_IF" info >/dev/null 2>&1 && iw dev "$AP_IF" del >/dev/null 2>&1 || true
  policy_off
  ip link set wlan0 up >/dev/null 2>&1 || true
  nmcli device set wlan0 managed yes >/dev/null 2>&1 || true
  nmcli device wifi rescan >/dev/null 2>&1 || true
  sleep 2
  while IFS=: read -r cn ac typ; do
    [ "$ac" = "yes" ] || continue
    [ "$cn" = "$AP_CON" ] && continue
    [ "$typ" = "802-11-wireless" ] || continue
    if timeout 15 nmcli connection up "$cn" >/dev/null 2>&1; then
      log "扫描成功，已回连正常 WiFi: $cn"
      rm -f "$OPENED" "$RECONNECT_STAMP"
      echo 0 > "$STATE"
      return 0
    fi
  done < <(nmcli -t -f NAME,AUTOCONNECT,TYPE connection show 2>/dev/null || true)
  log "扫描失败，恢复 ARCH 热点"
  start_ap >/dev/null 2>&1 || true
  if nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$AP_CON"; then
    policy_on
    echo 1 > "$STATE"
  fi
  return 1
}

WIRED_OK=0; USB_NET_OK=0; WIFI_OK=0; WIRED_IF=""; USB_IF=""
while IFS=: read -r dev type state con; do
  [ "$type" = "ethernet" ] || continue
  if is_builtin "$dev"; then WIRED_IF="$dev"; [ "$state" = "connected" ] && WIRED_OK=1
  else USB_IF="$dev"; [ "$state" = "connected" ] && USB_NET_OK=1; fi
done < <(nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status 2>/dev/null)
nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null | grep -q '^[^:]*:wifi:connected$' && WIFI_OK=1

WANT=0; REASON=""
if [ "$USB_NET_OK" = "0" ] && [ "$WIRED_OK" = "0" ] && [ "$WIFI_OK" = "0" ]; then
  [ -f "$DOWN_SINCE" ] || date +%s > "$DOWN_SINCE"
  NOW=$(date +%s); SINCE=$(cat "$DOWN_SINCE" 2>/dev/null || echo "$NOW")
  [ $((NOW - SINCE)) -ge 10 ] && { WANT=1; REASON="有线、WiFi、4G 全部未连接超过 10 秒"; }
else
  rm -f "$DOWN_SINCE"
fi

CUR=0; [ -f "$STATE" ] && CUR=$(cat "$STATE" 2>/dev/null || echo 0)
if [ "$WANT" = "1" ]; then
  if start_ap; then
    policy_on
    [ -f "$OPENED" ] || date +%s > "$OPENED"
    if [ "$CUR" != "1" ]; then
      echo 1 > "$STATE"
      log "热点已开启（$REASON）SSID=$AP_SSID 接口=$AP_IF；客户端流量已阻断，AP持续在线直到网络恢复"
    fi
    try_upstream_pause || true
  fi
else
  close_ap
  if [ "$CUR" != "0" ]; then log "热点已关闭（正常网络已恢复）"; fi
fi
exit 0
