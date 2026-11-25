#!/bin/bash
# [1.0.144] 供电/降频自检：Pi 欠压或降频会导致 SDR 发射丢块（TETRA/DMR 空口不通）
set -u
T=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
[ -n "$T" ] || T=unknown
# [1.0.154] 把状态写出来给网页读（/api/power）
mkdir -p /run 2>/dev/null || true
printf 'throttled=%s\n' "$T" > /run/arch-power.state 2>/dev/null || true
if [ "$T" = "0x0" ] || [ "$T" = "0" ]; then
  echo "[power] OK throttled=$T"
  exit 0
fi
MSG="[power] 警告：检测到欠压/降频 throttled=$T —— 请更换 5V/3A 以上电源与线材，否则 SDR 发射会丢块（表现为本机模式空口不通）"
echo "$MSG"
logger -t arch-power "$MSG" 2>/dev/null || true
exit 1
