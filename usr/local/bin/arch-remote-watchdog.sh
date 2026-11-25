#!/bin/bash
# [FIX 1.0.98] ARCH 远程维护通道看门狗
# 背景：设备重启/升级后，arch-remote-agent 出现过"隧道在、agent 不响应"的情况
# （北京侧连接被接受后一直挂住、CLOSE_WAIT），导致无法远程维护。
# 策略：
#   1) agent 服务不在 -> 重启 agent + 隧道
#   2) 本地 42172 端口连不上 -> 重启 agent + 隧道
#   3) 每 2 小时主动重启一次（防止长跑后 accept/子进程挂死）
#
# [1.0.148] 两处放宽（高负载误判会把正在跑的安装/调试会话踢掉）：
#   · 端口探测失败改为【连续 FAIL_NEED 次】才重启（timer 5 分钟一次 => 至少 15 分钟没有恢复才动手）
#   · 升级窗口（/run/arch-upgrade.lock，45 分钟内有效）只检查"服务在不在"，不做端口探测重启
set -u
AGENT_PORT=42172
STAMP=/run/arch-remote-watchdog.stamp
FAIL=/run/arch-remote-watchdog.fail
UPGRADE_LOCK=/run/arch-upgrade.lock
FAIL_NEED=3
PROBE_WAIT=6

restart_both() {
  systemctl restart arch-remote-agent.service 2>/dev/null || true
  systemctl restart arch-remote-tunnel.service 2>/dev/null || true
  echo "[$(date '+%F %T')] restarted remote agent+tunnel"
}

# 1) agent 服务不在 -> 立即重启（这是真故障）
if ! systemctl is-active --quiet arch-remote-agent.service; then
  rm -f "$FAIL"
  restart_both
  exit 0
fi

# 2) 升级窗口：只认"服务在不在"，端口忙不忙一律不管
if [ -f "$UPGRADE_LOCK" ]; then
  if [ -n "$(find "$UPGRADE_LOCK" -mmin -45 2>/dev/null)" ]; then
    exit 0
  fi
  rm -f "$UPGRADE_LOCK"
fi

# 3) 【1.0.153】握手探测：不光看端口能不能连，还要看 agent 会不会回 READY。
#    原来只测 TCP 连接，遇到"连上了但不回话"的假死 agent 永远不重启（实测：后台显示在线、远程连不进去）。
probe_agent() {
  timeout "$PROBE_WAIT" python3 - >/dev/null 2>&1 <<'PY'
import socket, sys
try:
    with open("/etc/mmdvm/remote-debug.secret", encoding="ascii") as f:
        secret = f.read().strip()
    if len(secret) < 32:
        sys.exit(1)
    s = socket.create_connection(("127.0.0.1", 42172), timeout=3)
    s.sendall((secret + "\n").encode("ascii"))
    s.settimeout(3)
    sys.exit(0 if b"READY" in s.recv(64) else 1)
except Exception:
    sys.exit(1)
PY
}
if probe_agent; then
  rm -f "$FAIL"
else
  n=$(cat "$FAIL" 2>/dev/null || echo 0)
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  n=$((n + 1))
  echo "$n" > "$FAIL"
  if [ "$n" -ge "$FAIL_NEED" ]; then
    rm -f "$FAIL"
    restart_both
    exit 0
  fi
  echo "[$(date '+%F %T')] agent 端口探测失败第 ${n}/${FAIL_NEED} 次，暂不重启"
fi

# 4) 每 2 小时主动重启一次（防止长跑后 accept/子进程挂死）
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
if [ "$LAST" -eq 0 ]; then echo "$NOW" > "$STAMP"; exit 0; fi
if [ $((NOW - LAST)) -gt 7200 ]; then echo "$NOW" > "$STAMP"; restart_both; fi
exit 0
