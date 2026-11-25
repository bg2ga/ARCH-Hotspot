#!/bin/bash
set -u
echo "ARCH 热点诊断"
echo "主机: $(hostname)  时间: $(date '+%F %T')"
for u in mmdvm-multi mmdvm-web dmrgateway fmo-bridge nginx mosquitto; do printf '%-18s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null)"; done
for i in 1 2 3 4 5 6 7; do printf '信道 %d: IQ=%s Host=%s\n' "$i" "$(systemctl is-active mmdvm-iq@$i 2>/dev/null)" "$(systemctl is-active mmdvmhost@$i 2>/dev/null)"; done
echo "网络监听:"; ss -lun 2>/dev/null | grep -E ':(3334|3335|4810|4820|49880|62031|62032)\b' || true