#!/bin/bash
# [1.0.145] TETRA 模式瘦身：切到 TETRA 时停掉"没有 MMDVM 就没有意义"的常驻组件，
#   给树莓派 3B 这类弱机型腾出 CPU / 内存（TETRA 是硬实时负载，抢核会丢 TX 块）。
#
#   安全底线（务必遵守）：
#     1) 只动下面白名单里的单元；网络 / 远程 / Web / 射频 / systemd 一个都不碰；
#     2) 只 stop 不 disable —— 重启后一切照旧，绝不会"停完就起不来"；
#     3) restore 只拉起"仍然是 enabled 的"单元，绝不启动用户主动禁用的服务；
#     4) 幂等：重复执行、部分失败都无害，任何一步失败都不影响切换。
set -u

# 白名单：全是"没有 MMDVM 就没有意义"的网关/采集器，以及与无线电无关的可选守护进程
SLIM_UNITS="dmrgateway nxdngateway ysfgateway p25gateway ircddbgatewayd \
arch-qso-live arch-gateway-status bluetooth"

log(){ echo "[slim] $*"; }
is_enabled(){ [ "$(systemctl is-enabled "$1" 2>/dev/null)" = "enabled" ]; }

case "${1:-status}" in
  stop)
    for u in $SLIM_UNITS; do
      systemctl is-active --quiet "$u" 2>/dev/null || continue
      is_enabled "$u" || continue          # 只动会被系统自动拉起的服务，用户禁用的不碰
      systemctl stop "$u" 2>/dev/null && log "stopped $u"
    done
    log "stop done"
    ;;
  restore)
    for u in $SLIM_UNITS; do
      is_enabled "$u" || continue          # 用户主动禁用的不启动
      systemctl is-active --quiet "$u" 2>/dev/null && continue
      systemctl start "$u" 2>/dev/null && log "started $u"
    done
    log "restore done"
    ;;
  status)
    for u in $SLIM_UNITS; do
      printf '%-26s enabled=%-9s active=%s\n' "$u" \
        "$(systemctl is-enabled "$u" 2>/dev/null)" "$(systemctl is-active "$u" 2>/dev/null)"
    done
    ;;
  *)
    echo "用法: arch-mode-slim.sh {stop|restore|status}"; exit 2;;
esac
exit 0