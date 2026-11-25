#!/bin/bash
# [FIX 1.0.127] ARCH 热点 -> 北京 反向管理隧道（远程命令 + 打开Web 共用）。
# 关键改进：优先走 443 端口。实测部分运营商会对出站 TCP/22 做 DPI 重置
#   （kex_exchange_identification: read: Connection reset by peer），
#   使隧道反复重连、后台"打开Web"与远程命令全部不可用；443 通常放行，失败回退 22。
# 同时加了退避，避免高频重连触发对端限速。
set -u
FLAG=/etc/mmdvm/remote-debug.enable
KEY=/etc/mmdvm/arch-tunnel.key
SECRET_FILE=/etc/mmdvm/remote-debug.secret
[ -f "$FLAG" ] && [ "$(cat "$FLAG" 2>/dev/null)" = "0" ] && exit 0
[ -s "$KEY" ] || { logger -t arch-remote-tunnel "missing $KEY"; exit 1; }
CS="$(grep -m1 '^Callsign=' /etc/MMDVM-Host.ini 2>/dev/null | cut -d= -f2 | tr -d ' \r\n')"
[ -n "$CS" ] || CS="NOCALL"
DID="$(/usr/local/bin/arch-device-id 2>/dev/null | tr -d ' \r\n' | head -c 64)"
[ -n "$DID" ] || DID="arch-unknown"
SECRET="$(cat "$SECRET_FILE" 2>/dev/null | tr -d ' \r\n')"
[ -n "$SECRET" ] || { logger -t arch-remote-tunnel "missing $SECRET_FILE"; exit 1; }
H="$(printf '%s' "${SECRET}-${DID}" | sha256sum | cut -c1-8)"
PORT=$(( 30000 + 0x${H} % 20000 ))
WEBPORT=$(( 10000 + 0x${H} % 20000 ))
mkdir -p /etc/mmdvm
echo "$CS $DID $PORT $WEBPORT" > /etc/mmdvm/remote-tunnel.port

HOST="${ARCH_TUNNEL_HOST:-39.96.117.224}"
PORTS="${ARCH_TUNNEL_PORTS:-443 22}"
SSH_OPTS="-N -i $KEY -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -o TCPKeepAlive=yes -o ConnectTimeout=12"
while :; do
  for P in $PORTS; do
    ssh $SSH_OPTS -p "$P" \
      -R 127.0.0.1:${PORT}:127.0.0.1:42172 \
      -R 127.0.0.1:${WEBPORT}:127.0.0.1:80 arch-tunnel@${HOST}
    rc=$?
    logger -t arch-remote-tunnel "ssh ${HOST}:${P} exited rc=${rc}" 2>/dev/null || true
    sleep 3
  done
  sleep 15
done
