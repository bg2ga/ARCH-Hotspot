#!/bin/bash
# arch-factory-reset.sh - ARCH 热点一键恢复出厂设置（恢复用户配置为出厂默认）
# 恢复：模式/信道规划、各模式网关、FMO 身份与证书、FM 哑音/尾音等用户配置
# 默认保留：固件版本、设备ID、远程管理(隧道/调测)、WiFi 与网络设置（保证仍可访问）
# 可选：--reset-network 同时清除 WiFi/网络配置；--dry-run 只列清单不改动
# 出厂默认快照由安装包提供，解压后是一个迷你根文件系统：
#   $FDIR/.ok  $FDIR/etc/...  $FDIR/opt/fmo-bridge/config.json
set -u
FDIR=/var/lib/arch/factory-defaults
LOG=/var/log/arch-factory-reset.log
DRY=0; KEEP_NET=1
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --reset-network) KEEP_NET=0 ;;
  esac
done
log(){ local m="[$(date '+%F %T')] $*"; echo "$m"; echo "$m" >>"$LOG" 2>/dev/null || true; }

ETC_FILES="MMDVM-Host.ini MMDVM-IQ.ini MMDVM-Multi.ini DMRGateway.ini YSFGateway.ini P25Gateway.ini NXDNGateway.ini"
MMDVM_JSON="channels.json frequency.json active-mode.conf fm-tailtone.json install-state.conf bm-masters.json"

if [ ! -d "$FDIR" ] || [ ! -f "$FDIR/.ok" ]; then
  echo "错误：缺少出厂默认快照 $FDIR（该设备可能是升级安装而来，请先升级到含快照的固件）。"; exit 3
fi

log "=== 出厂重置开始 (dry_run=$DRY keep_network=$KEEP_NET) ==="

# --- 1. 备份当前全部配置 ---
TS=$(date +%Y%m%d-%H%M%S); BAKDIR=/var/backups/mmdvm; BAK="$BAKDIR/factory-reset-$TS.tar.gz"
if [ "$DRY" = "0" ]; then
  mkdir -p "$BAKDIR"
  tar -czf "$BAK" \
    /etc/MMDVM-Host.ini /etc/MMDVM-IQ.ini /etc/MMDVM-Multi.ini \
    /etc/MMDVM-Host-*.ini /etc/MMDVM-IQ-*.ini \
    /etc/DMRGateway.ini /etc/YSFGateway.ini /etc/P25Gateway.ini /etc/NXDNGateway.ini \
    /etc/ircddbgateway /etc/mmdvm /opt/fmo-bridge \
    /etc/wpa_supplicant/wpa_supplicant.conf /etc/NetworkManager/system-connections \
    2>/dev/null || true
  log "已备份当前配置 -> $BAK"
else
  log "[dry-run] 将备份当前配置到 $BAK"
fi

# --- 2. 停止热点相关服务 ---
if [ "$DRY" = "0" ]; then
  systemctl stop fmo-bridge dmrgateway ysfgateway p25gateway nxdngateway ircddbgatewayd mmdvm-multi 2>/dev/null || true
  for n in $(seq 1 12); do systemctl stop "mmdvmhost@$n" "mmdvm-iq@$n" 2>/dev/null || true; done
  log "已停止热点相关服务"
else
  log "[dry-run] 将停止 mmdvm-multi / 各通道 / 各网关 / fmo-bridge"
fi

# --- 3. 还原基础模板 /etc/*.ini ---
for f in $ETC_FILES; do
  if [ -f "$FDIR/etc/$f" ]; then
    if [ "$DRY" = "0" ]; then install -m 0644 "$FDIR/etc/$f" "/etc/$f"; fi
    log "还原 /etc/$f"
  fi
done
# --- 3b. D-STAR 网关配置（/etc/ircddbgateway 是文件） ---
if [ -f "$FDIR/etc/ircddbgateway" ]; then
  if [ "$DRY" = "0" ]; then install -m 0644 "$FDIR/etc/ircddbgateway" /etc/ircddbgateway; fi
  log "还原 /etc/ircddbgateway"
fi

# --- 4. 还原 mmdvm JSON 配置 ---
if [ "$DRY" = "0" ]; then mkdir -p /etc/mmdvm; fi
for f in $MMDVM_JSON; do
  if [ -f "$FDIR/etc/mmdvm/$f" ]; then
    if [ "$DRY" = "0" ]; then cp -f "$FDIR/etc/mmdvm/$f" "/etc/mmdvm/$f"; chmod 0666 "/etc/mmdvm/$f" 2>/dev/null || true; fi
    log "还原 /etc/mmdvm/$f"
  fi
done

# --- 5. FMO 配置与证书 ---
if [ -f "$FDIR/opt/fmo-bridge/config.json" ]; then
  if [ "$DRY" = "0" ]; then
    install -m 0644 "$FDIR/opt/fmo-bridge/config.json" /opt/fmo-bridge/config.json
    rm -rf /opt/fmo-bridge/pki/* 2>/dev/null || true
  fi
  log "还原 /opt/fmo-bridge/config.json 并清除已注册证书（FMO 需重新注册）"
fi

# --- 6. 用默认信道规划重建各通道 INI ---
if [ -f /usr/local/libexec/channel-setup.py ] && [ -f /etc/mmdvm/channels.json ]; then
  if [ "$DRY" = "0" ]; then
    python3 /usr/local/libexec/channel-setup.py apply "$(cat /etc/mmdvm/channels.json)" >>"$LOG" 2>&1 && log "已按默认规划重建通道配置" || log "警告：channel-setup apply 失败（见 $LOG）"
  else
    log "[dry-run] 将执行 channel-setup.py apply <默认规划>"
  fi
fi

# --- 7. 可选：清除网络/WiFi ---
if [ "$KEEP_NET" = "0" ]; then
  if [ "$DRY" = "0" ]; then
    rm -f /etc/wpa_supplicant/wpa_supplicant.conf 2>/dev/null || true
    if command -v nmcli >/dev/null 2>&1; then
      nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="802-11-wireless"{print $1}' | while read -r c; do
        [ -n "$c" ] && nmcli connection delete "$c" >/dev/null 2>&1 || true
      done
    fi
    rm -rf /etc/NetworkManager/system-connections/* 2>/dev/null || true
  fi
  log "已清除 WiFi/网络连接配置（设备可能需要重新配网）"
else
  log "保留 WiFi/网络设置"
fi

# --- 8. 拉起服务 ---
if [ "$DRY" = "0" ]; then
  systemctl daemon-reload 2>/dev/null || true
  systemctl restart mmdvm-multi 2>/dev/null || true
  /usr/local/bin/sys-manage.sh hotspot-heal >/dev/null 2>&1 || true
  systemctl restart fmo-bridge 2>/dev/null || true
  log "已重新拉起热点服务"
fi
log "=== 出厂重置完成；配置备份：$BAK ==="
if [ "$DRY" = "0" ]; then echo "OK $BAK"; fi
exit 0
