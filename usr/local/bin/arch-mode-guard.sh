#!/bin/bash
# [1.0.143] 开机模式守卫：保证 MMDVM 与 TETRA 任何时候只有一侧在运行/自启。
#   根因：install.sh 每次升级都会无条件 enable 常规模式；若设备之前选过 TETRA，
#   开机时两侧同时启动 -> 抢同一块 SX1255 -> TETRA 卡在 activating 起不来。
# [1.0.145] TETRA 模式瘦身：切到 TETRA 时停掉与射频无关的常驻组件，给树莓派 3B
#   这类弱机型腾出 CPU/内存（TETRA 是硬实时负载，降频/抢核会丢 TX 块）。
#   ★ 安全底线：网络(NetworkManager/wpa_supplicant/nginx/mmdvm-web/ssh)、远程
#     (arch-remote-tunnel/arch-remote-agent/看门狗)、dbus/systemd/cron/日志 一律不动 ——
#     保证任何时候都能连上设备、打开 Web、也能切回常规模式。
#   瘦身清单与实现统一放在 /usr/local/bin/arch-mode-slim.sh（只 stop 不 disable，幂等）。
set -u
MODE="$(cat /opt/tetra/desired-mode 2>/dev/null | tr -d ' \r\n')"
[ -n "$MODE" ] || MODE=mmdvm
UNITS="mmdvm-multi"
for n in $(seq 1 12); do UNITS="$UNITS mmdvm-iq@$n mmdvmhost@$n"; done
SLIM=/usr/local/bin/arch-mode-slim.sh
log(){ echo "[mode-guard] $*"; }
slim(){ [ -x "$SLIM" ] && "$SLIM" "$1" >/dev/null 2>&1; return 0; }

if [ "$MODE" = "tetra" ]; then
  log "desired-mode=tetra -> 停常规模式 + 瘦身，拉起 TETRA"
  systemctl stop $UNITS 2>/dev/null
  systemctl disable $UNITS 2>/dev/null
  slim stop
  systemctl enable tetra-bs.service 2>/dev/null
  systemctl start tetra-bs.service 2>/dev/null
  log "tetra-bs=$(systemctl is-active tetra-bs 2>/dev/null) mmdvm-multi=$(systemctl is-active mmdvm-multi 2>/dev/null)"
else
  log "desired-mode=$MODE -> 停用 TETRA，恢复常规模式（含瘦身组件）"
  systemctl stop tetra-bs.service 2>/dev/null
  systemctl disable tetra-bs.service 2>/dev/null
  # 先恢复被瘦身停掉的可选组件，再拉起常规射频（慢几秒无妨）
  slim restore
  sleep 2
  if [ -f /etc/mmdvm/channels.json ]; then
    N=$(python3 -c "import json;print(int(json.load(open('/etc/mmdvm/channels.json')).get('channels') or 1))" 2>/dev/null || echo 1)
  else
    N=1
  fi
  systemctl enable --now mmdvm-multi.service 2>/dev/null
  i=1
  while [ "$i" -le "$N" ]; do
    [ -f "/etc/MMDVM-IQ-$i.ini" ] && systemctl enable --now "mmdvm-iq@$i.service" 2>/dev/null
    [ -f "/etc/MMDVM-Host-$i.ini" ] && systemctl enable --now "mmdvmhost@$i.service" 2>/dev/null
    i=$((i+1))
  done
  log "mmdvm-multi=$(systemctl is-active mmdvm-multi 2>/dev/null) tetra-bs=$(systemctl is-active tetra-bs 2>/dev/null)"
fi
exit 0