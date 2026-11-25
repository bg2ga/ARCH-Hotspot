#!/usr/bin/env bash
set -euo pipefail
# ARCH: make USB/4G network adapters managed and DHCP automatically.
while IFS=: read -r IF TYPE; do
  case "$IF" in ''|--|lo|eth0|wlan0) continue ;; esac
  case "$TYPE" in ethernet|gsm|wwan) ;; *) continue ;; esac
  nmcli device set "$IF" managed yes >/dev/null 2>&1 || true
  STATE=$(nmcli -t -f DEVICE,STATE dev status 2>/dev/null | awk -F: -v d="$IF" '$1==d{print $2; exit}')
  [ "$STATE" = "connected" ] && continue
  if [ "$TYPE" = "ethernet" ]; then
    CON="arch-usb-$IF"
    if ! nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq "$CON"; then
      nmcli connection add type ethernet ifname "$IF" con-name "$CON" \
        ipv4.method auto ipv6.method auto autoconnect yes >/dev/null 2>&1 || true
    fi
    nmcli connection up "$CON" >/dev/null 2>&1 || true
  else
    nmcli device connect "$IF" >/dev/null 2>&1 || true
  fi
done < <(nmcli -t -f DEVICE,TYPE dev status 2>/dev/null)
