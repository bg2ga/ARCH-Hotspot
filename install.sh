#!/usr/bin/env bash
set -euo pipefail

# ARCH multi-core install: detect 1/2/4/many-core CPU safely.
CPU_CORES=$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 1)
case "$CPU_CORES" in ''|*[!0-9]*|0) CPU_CORES=1;; esac
[ "$CPU_CORES" -gt 4 ] && CPU_CORES=4
export MAKEFLAGS="-j$CPU_CORES ${MAKEFLAGS:-}"
export CMAKE_BUILD_PARALLEL_LEVEL="$CPU_CORES"
export PIP_DISABLE_PIP_VERSION_CHECK=1
ROOT="$(cd "$(dirname "$0")" && pwd)"
# 版本/通道以包内 VERSION / CHANNEL 文件为准（原来写死会导致装完仍上报旧版本，
# 于是"更新完重启又提示有更新"；公开版还会被切到内测通道）。
if [ -s "$ROOT/CHANNEL" ]; then CHANNEL="$(tr -d '\r\n' < "$ROOT/CHANNEL")"; else CHANNEL="internal"; fi
if [ -s "$ROOT/VERSION" ]; then VERSION="$(tr -d '\r\n' < "$ROOT/VERSION")"; else VERSION="1.0.72"; fi
if [ "$CHANNEL" = "public" ]; then
  systemctl disable --now tetra-bs.service >/dev/null 2>&1 || true
  rm -f /opt/tetra/desired-mode
fi

# [1.0.147] 当前模式感知。desired-mode=tetra 时安装脚本绝不能"顺手把常规热点拉起来"：
#   ①MMDVM 与 TETRA 基站会抢同一块 SX1255；
#   ②紧接着的"引擎健康自检"必然发现 mmdvm-multi 没在跑 → 误判"新版引擎未能运行"→
#     回退引擎并打印警告（实测：用户升级后第一次切 TETRA 100% 失败一次）。
#   /opt/tetra/desired-mode 就是模式守卫/tetra-api 用的那份权威文件。
CUR_MODE="$(cat /opt/tetra/desired-mode 2>/dev/null | tr -d ' \r\n' || true)"
if [ -z "$CUR_MODE" ]; then CUR_MODE=mmdvm; fi
MMDVM_START=1
if [ "$CUR_MODE" = "tetra" ]; then MMDVM_START=0; fi
echo "当前模式=$CUR_MODE（TETRA 模式下不启动常规射频）"
# [1.0.148] 升级窗口标记：远程看门狗在这个窗口内只检查"服务在不在"，
# 不做端口探测重启 —— 否则升级高负载时它会误判"agent 无响应"并重启 agent+tunnel，
# 把正在跑安装的会话踢掉（安装本身不会坏，但会看不到日志、要重连）。脚本结尾清除。
touch /run/arch-upgrade.lock 2>/dev/null || true
# 统一入口：常规模式=启动+置开机自启；TETRA 模式=只置开机自启，绝不启动常规射频
unit_up() {
  if [ "$MMDVM_START" -eq 1 ]; then
    systemctl enable --now "$@" >/dev/null 2>&1 || true
  else
    systemctl enable "$@" >/dev/null 2>&1 || true
  fi
  return 0
}

sync_ircddb_hosts() {
  install -d -m 0755 /usr/share/ircddbgateway
  for f in DCS_Hosts.txt DPlus_Hosts.txt DExtra_Hosts.txt CCS_Hosts.txt XLX_Hosts.txt; do
    if [ -f "/usr/local/etc/hosts/$f" ]; then
      ln -sfn "/usr/local/etc/hosts/$f" "/usr/share/ircddbgateway/$f"
    fi
  done
  return 0
}


configure_live_events() {
  install -d -m 0755 /usr/local/libexec
  chmod 0755 /usr/local/bin/* 2>/dev/null || true
  [ -f "$ROOT/usr/local/bin/arch-sx-probe" ] && install -m 0755 "$ROOT/usr/local/bin/arch-sx-probe" /usr/local/bin/arch-sx-probe
  cat > /etc/sudoers.d/arch-web <<'SUDO'
Defaults:mmdvm-web !requiretty
mmdvm-web ALL=(root) NOPASSWD: /usr/local/bin/web-manage.sh *, /usr/local/bin/web-fmo.sh *, /usr/local/bin/web-multi.sh *, /usr/local/bin/sys-manage.sh *, /usr/local/bin/web-tetra.sh *, /usr/local/libexec/mmdvm-save.py *, /usr/local/libexec/hosts-build.py *
SUDO
  chmod 0440 /etc/sudoers.d/arch-web
  visudo -cf /etc/sudoers.d/arch-web >/dev/null
  install -m 0755 "$ROOT/usr/local/libexec/arch-qso-live.py" /usr/local/libexec/arch-qso-live.py
  install -m 0644 "$ROOT/etc/systemd/system/arch-qso-live.service" /etc/systemd/system/arch-qso-live.service
  for n in $(seq 1 12); do
    f="/etc/MMDVM-Host-$n.ini"
    [ -f "$f" ] || continue
    sed -i -E "s/^Name=mmdvmhost-?[0-9]*$/Name=mmdvmhost$n/" "$f"
  done
  systemctl daemon-reload
  systemctl enable --now arch-qso-live.service >/dev/null 2>&1 || true
  python3 "$ROOT/usr/local/libexec/arch-fm-audio-baseline.py" || true
}

restart_live_event_hosts() {
  local n
  n="$(python3 -c 'import json;print(int(json.load(open("/etc/mmdvm/channels.json")).get("channels",6)))' 2>/dev/null || echo 6)"
  # [1.0.147] TETRA 模式下不启动常规射频（只补开机自启），避免与 TETRA 基站抢 SX1255
  if [ "$MMDVM_START" -eq 1 ]; then
    systemctl start mmdvm-multi.service 2>/dev/null || true
  fi
  sleep 1
  for i in $(seq 1 "$n"); do
    unit_up "mmdvm-iq@$i.service"
    unit_up "mmdvmhost@$i.service"
    if [ "$MMDVM_START" -eq 1 ]; then
      systemctl try-restart "mmdvmhost@$i.service" 2>/dev/null || true
    fi
  done
}

FAST_MARK='ARCH_FAST_PATCH_1_0_50'
CURVER="$(tr -d '\r\n' < /etc/mmdvm/arch-build-version 2>/dev/null || true)"
case "$CURVER" in
  1.0.47|1.0.48|1.0.49|1.0.50|1.0.51|1.0.52|1.0.53|1.0.54|1.0.55|1.0.56|1.0.57|1.0.58|1.0.59|1.0.60|1.0.61|1.0.62|1.0.63|1.0.64|1.0.65|1.0.66|1.0.67|1.0.68|1.0.69|1.0.70|1.0.71|1.0.72|1.0.73|1.0.74|1.0.75)
    touch /etc/mmdvm/fm-ctcss-tx.enable
chmod 0666 /etc/mmdvm/fm-ctcss-tx.enable
sync_ircddb_hosts
    install -m 0755 "$ROOT/opt/fmo-bridge/fmo_bridge.py" /opt/fmo-bridge/fmo_bridge.py
    install -m 0644 "$ROOT/opt/mmdvm-web/app.py" /opt/mmdvm-web/app.py
    python3 -m py_compile /opt/fmo-bridge/fmo_bridge.py /opt/mmdvm-web/app.py
    install -m 0755 "$ROOT/usr/local/libexec/arch-gateway-autoconnect.py" /usr/local/libexec/arch-gateway-autoconnect.py
    install -m 0755 "$ROOT/usr/local/libexec/gateway-status-collector.py" /usr/local/libexec/gateway-status-collector.py
    configure_live_events
    restart_live_event_hosts
    install -m 0644 "$ROOT/etc/systemd/system/arch-gateway-status.service" /etc/systemd/system/arch-gateway-status.service
    install -m 0644 "$ROOT/etc/systemd/system/arch-gateway-autoconnect.service" /etc/systemd/system/arch-gateway-autoconnect.service
    systemctl daemon-reload
    systemctl enable --now arch-gateway-status.service arch-gateway-autoconnect.service >/dev/null 2>&1 || true
    python3 /usr/local/libexec/arch-gateway-autoconnect.py
    printf '%s\n' "$VERSION" > /etc/mmdvm/arch-build-version
    cat > /etc/arch-hotspot-release <<EOF
CHANNEL=$CHANNEL
VERSION=$VERSION
UPDATE_BASE=https://hljrsa.cn:50113/releases/ota/arch-hotspot
EOF
    systemctl restart fmo-bridge.service
    systemctl restart ircddbgatewayd.service
    systemctl restart mmdvm-web.service
    echo "ARCH 热点 $CHANNEL $VERSION 快速补丁安装完成"
    exit 0
    ;;
esac
if [ "$(id -u)" -ne 0 ]; then echo "请使用 sudo 运行"; exit 1; fi
SERVICE_CHECK_DONE=0
configured_units() {
python3 - <<'PY'
import json, re
req={'mosquitto','nginx','mmdvm-web'}
try:
    plan=json.load(open('/etc/mmdvm/channels.json'))
except Exception:
    plan={}
modes=set()
for x in (plan.get('list') or []):
    if isinstance(x,dict) and str(x.get('mode') or '').strip():
        modes.add(str(x.get('mode')).upper())
try:
    ini=open('/etc/DMRGateway.ini',errors='ignore').read()
    m=re.search(r'(?mi)^\s*Id\s*=\s*(\d+)',ini)
    if m and int(m.group(1))>0 and 'DMR' in modes:
        req.add('dmrgateway')
except Exception:
    pass
if 'D-STAR' in modes or 'DSTAR' in modes:
    req.add('ircddbgatewayd')
if 'P25' in modes:
    req.add('p25gateway')
if 'NXDN' in modes:
    req.add('nxdngateway')
if 'YSF' in modes:
    req.add('ysfgateway')
if 'FM' in modes or 'FMO' in modes or 'ANALOG' in modes:
    req.add('fmo-bridge')
print(' '.join(sorted(req)))
PY
}
ensure_services_up() {
  if [ "$SERVICE_CHECK_DONE" = 1 ]; then return 0; fi
  SERVICE_CHECK_DONE=1
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl enable --now mosquitto nginx mmdvm-web.service >/dev/null 2>&1 || true
  systemctl reset-failed mmdvm-multi.service >/dev/null 2>&1 || true
  unit_up mmdvm-multi.service
  local n
  n=$(python3 -c 'import json;print(int(json.load(open("/etc/mmdvm/channels.json")).get("channels",6)))' 2>/dev/null || echo 6)
  local i
  for i in $(seq 1 "$n"); do
    systemctl reset-failed "mmdvm-iq@$i.service" "mmdvmhost@$i.service" >/dev/null 2>&1 || true
    unit_up "mmdvm-iq@$i.service" "mmdvmhost@$i.service"
  done
  local units
  units=$(configured_units)
  for u in $units; do
    systemctl reset-failed "$u" >/dev/null 2>&1 || true
    unit_up "$u"
  done
  systemctl enable --now arch-remote-agent.service arch-remote-tunnel.service >/dev/null 2>&1 || true
  systemctl enable --now arch-remote-watchdog.timer >/dev/null 2>&1 || true
  sleep 2
  local bad=0
  # [1.0.147] TETRA 模式下 mmdvm-multi 本就不该在跑，不能当成"必需服务未运行"来告警
  local chk="mmdvm-web nginx mosquitto $units"
  if [ "$MMDVM_START" -eq 1 ]; then chk="mmdvm-multi $chk"; fi
  for u in $chk; do
    if ! systemctl is-active --quiet "$u"; then
      echo "警告：必需服务 $u 未运行"
      systemctl --no-pager -l status "$u" 2>/dev/null | tail -8 || true
      bad=1
    fi
  done
  if [ "$MMDVM_START" -eq 1 ]; then
    for i in $(seq 1 "$n"); do
      [ -f "/etc/MMDVM-IQ-$i.ini" ] || continue
      if ! systemctl is-active --quiet "mmdvm-iq@$i.service"; then echo "警告：CH$i MMDVM-IQ 未运行"; bad=1; fi
      if [ -f "/etc/MMDVM-Host-$i.ini" ] && ! systemctl is-active --quiet "mmdvmhost@$i.service"; then echo "警告：CH$i MMDVM-Host 未运行"; bad=1; fi
    done
  else
    echo "当前模式=TETRA：跳过常规射频通道检查"
  fi
  return 0
}
trap ensure_services_up EXIT

export DEBIAN_FRONTEND=noninteractive
FIRST_INSTALL=0
[ -f /etc/arch-hotspot-release ] || FIRST_INSTALL=1
APT_UPDATED=0
NEED_APT=0
for p in python3 python3-pip nginx pigz network-manager git curl wget ca-certificates alsa-utils iptables iw dnsmasq-base; do
  dpkg -s "$p" >/dev/null 2>&1 || { NEED_APT=1; break; }
done
if [ "$FIRST_INSTALL" = 1 ] || [ "$NEED_APT" = 1 ]; then
  apt-get update || true
  APT_UPDATED=1
  apt-get install -y --no-install-recommends python3 python3-pip nginx pigz network-manager git curl wget ca-certificates alsa-utils iptables iw dnsmasq-base || true
fi
if ! python3 -c 'import flask,gunicorn,paho.mqtt,cbor2,nacl' >/dev/null 2>&1; then
  if [ "$APT_UPDATED" = 0 ]; then apt-get update || true; APT_UPDATED=1; fi
  apt-get install -y --no-install-recommends python3-flask gunicorn python3-paho-mqtt python3-cbor2 python3-nacl || true
fi
if ! python3 -c 'import flask,gunicorn,paho.mqtt,cbor2,nacl' >/dev/null 2>&1; then
  mkdir -p /opt/arch-deps/wheels
  [ -f /opt/arch-deps/arch-python-wheels.tar.gz ] || curl -4 -fsSL --connect-timeout 8 --max-time 60 "https://arch.hljrsa.cn/downloads/arch-hotspot/deps/arch-python-wheels.tar.gz" -o /opt/arch-deps/arch-python-wheels.tar.gz
  tar -xzf /opt/arch-deps/arch-python-wheels.tar.gz -C /opt/arch-deps/wheels
  python3 -m pip install --break-system-packages --no-index --find-links=/opt/arch-deps/wheels Flask gunicorn paho-mqtt cbor2 pynacl
fi
install -m 0755 "$ROOT/build_upstream.sh" /usr/local/bin/arch-build-upstream
mkdir -p /etc/mmdvm
COMPONENT_REVISION="2026.09.29-fm-local-fix"
if [ ! -x /usr/local/bin/MMDVM-Multi ] || ! find /usr/local/lib /usr/lib -type f -name '*SXSupport*.so' 2>/dev/null | grep -q .; then
  /usr/local/bin/arch-build-upstream
elif [ -f /etc/mmdvm/arch-component-revision ] && [ "$(cat /etc/mmdvm/arch-component-revision 2>/dev/null || true)" != "$COMPONENT_REVISION" ]; then
  FORCE_MMDVM_IQ=1 /usr/local/bin/arch-build-upstream
fi
printf '%s\n' "$VERSION" > /etc/mmdvm/arch-build-version
printf '%s\n' "$COMPONENT_REVISION" > /etc/mmdvm/arch-component-revision
if find /usr/local/lib /usr/lib -type f -name '*SXSupport*.so' 2>/dev/null | grep -q .; then mkdir -p /etc/mmdvm; touch /etc/mmdvm/soapy-sx-verified; fi
getent passwd mmdvm-web >/dev/null || useradd --system --home /opt/mmdvm-web --shell /usr/sbin/nologin mmdvm-web
install -d -o www-data -g adm -m 0755 /var/log/nginx
install -d -m 0755 /opt/mmdvm-web /opt/mmdvm-web/data /opt/fmo-bridge /opt/fmo-bridge/pki /var/log/mmdvm /var/backups/mmdvm /etc/mmdvm /etc/systemd/system /usr/local/bin /usr/local/sbin /usr/local/libexec /usr/local/etc /usr/local/etc/hosts
if [ -d "$ROOT/opt/tetra-src" ]; then install -d -m 0755 /opt/tetra /opt/tetra-src/flowstation/target/release; install -m 0755 "$ROOT/opt/tetra-src/flowstation/target/release/bluestation-bs" /opt/tetra-src/flowstation/target/release/bluestation-bs; [ -f /opt/tetra/config.toml ] || install -m 0644 "$ROOT/opt/tetra/config.toml" /opt/tetra/config.toml; [ -f /opt/tetra/ui-config.json ] || install -m 0644 "$ROOT/opt/tetra/ui-config.json" /opt/tetra/ui-config.json; install -m 0755 "$ROOT/opt/tetra/reset_usb.sh" /opt/tetra/reset_usb.sh; fi
# ARCH 1.0.137: enable FlowStation dashboard on existing installs (used by Web status + 8080 config)
if [ -f /opt/tetra/config.toml ]; then
  python3 - <<'PY'
from pathlib import Path
p=Path('/opt/tetra/config.toml')
s=p.read_text(encoding='utf-8')
old='''# [dashboard]
# port = 8080                    # HTTP port the dashboard listens on (default: 8080; must not be 0)
# bind = "0.0.0.0"               # bind address (default: 0.0.0.0 = all interfaces; use 127.0.0.1 for localhost-only)'''
new='''[dashboard]
port = 8080
bind = "0.0.0.0"'''
if old in s:
    p.write_text(s.replace(old,new,1),encoding='utf-8')
elif '[dashboard]' not in s:
    p.write_text(s.rstrip()+'\n\n[dashboard]\nport = 8080\nbind = "0.0.0.0"\n',encoding='utf-8')
PY
fi
cp -a "$ROOT/opt/mmdvm-web/." /opt/mmdvm-web/
cp -a "$ROOT/opt/fmo-bridge/." /opt/fmo-bridge/
for u in $(systemctl list-units --type=service --state=running --no-legend 2>/dev/null | awk '{print $1}' | grep -E '^mmdvm-(iq|host)@'); do systemctl stop "$u" 2>/dev/null || true; done
systemctl stop mmdvm-multi.service dmrgateway.service 2>/dev/null || true

# === 射频引擎保护：升级前备份 MMDVM-Multi / MMDVM-IQ ===
# 这两个二进制决定收发解调。若新版引擎在本机跑不起来，下面会自动回退到旧引擎，
# 避免出现"升级后射频收不到/没有反应"且无法恢复的情况。
ENGINE_BAK="/var/backups/mmdvm/engine-before-${VERSION}-$(date +%Y%m%d-%H%M%S)"
install -d -m 0755 "$ENGINE_BAK"
for ef in MMDVM-Multi MMDVM-IQ; do
  if [ -x "/usr/local/bin/$ef" ]; then
    cp -a "/usr/local/bin/$ef" "$ENGINE_BAK/$ef" 2>/dev/null || true
    md5sum "/usr/local/bin/$ef" >> "$ENGINE_BAK/md5-before.txt" 2>/dev/null || true
  fi
done
echo "引擎已备份到 $ENGINE_BAK"

cp -a "$ROOT/usr/local/bin/." /usr/local/bin/
chmod 0755 /usr/local/bin/* 2>/dev/null || true
if [ -d "$ROOT/usr/local/sbin" ]; then install -d -m 0755 /usr/local/sbin; cp -a "$ROOT/usr/local/sbin/." /usr/local/sbin/; fi
chmod 0755 /usr/local/sbin/arch-mmdvm-multi-launch.sh 2>/dev/null || true
cp -a "$ROOT/usr/local/libexec/." /usr/local/libexec/
if [ -d "$ROOT/usr/local/share" ]; then install -d -m 0755 /usr/local/share; cp -a "$ROOT/usr/local/share/." /usr/local/share/; fi
if [ -d "$ROOT/usr/local/lib" ]; then cp -a "$ROOT/usr/local/lib/." /usr/local/lib/; fi
if [ -d "$ROOT/etc/NetworkManager/conf.d" ]; then
  install -d -m 0755 /etc/NetworkManager/conf.d
  cp -a "$ROOT/etc/NetworkManager/conf.d/." /etc/NetworkManager/conf.d/
fi
cp -a "$ROOT/etc/systemd/system/." /etc/systemd/system/
if [ -d "$ROOT/etc/udev/rules.d" ]; then install -d -m 0755 /etc/udev/rules.d; cp -a "$ROOT/etc/udev/rules.d/." /etc/udev/rules.d/; fi
if [ -d "$ROOT/etc/rpi/swap.conf.d" ]; then install -d -m 0755 /etc/rpi/swap.conf.d; cp -a "$ROOT/etc/rpi/swap.conf.d/." /etc/rpi/swap.conf.d/; fi
cp -a "$ROOT/etc/nginx/sites-available/mmdvm-web" /etc/nginx/sites-available/mmdvm-web
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/mmdvm-web /etc/nginx/sites-enabled/mmdvm-web
/usr/sbin/nginx -t
for f in channels.json frequency.json active-mode.conf fm-tailtone.json fw-url install-state.conf bm-masters.json; do [ -f "$ROOT/etc/mmdvm/$f" ] && cp -n "$ROOT/etc/mmdvm/$f" "/etc/mmdvm/$f"; done
for f in "$ROOT"/etc/*.ini; do
  [ -f "$f" ] || continue
  if [ "$FIRST_INSTALL" -eq 1 ]; then
    cp -f "$f" "/etc/$(basename "$f")"
  else
    cp -n "$f" "/etc/$(basename "$f")"
  fi
done
if [ -f "$ROOT/etc/ircddbgateway" ]; then
  if [ "$FIRST_INSTALL" -eq 1 ]; then
    cp -f "$ROOT/etc/ircddbgateway" /etc/ircddbgateway
  else
    cp -n "$ROOT/etc/ircddbgateway" /etc/ircddbgateway
  fi
fi
if [ ! -f /opt/fmo-bridge/config.json ]; then cp "$ROOT/opt/fmo-bridge/config.template.json" /opt/fmo-bridge/config.json; fi
python3 /usr/local/libexec/fmo-migrate-1.0.30.py || true
python3 /usr/local/libexec/arch-fm-audio-migrate.py || true
chmod 0755 /usr/local/bin/*.sh /usr/local/bin/*.py /usr/local/libexec/*.py 2>/dev/null || true
chmod 0755 /usr/local/bin/MMDVM-IQ /usr/local/bin/MMDVM-Multi /usr/local/bin/MMDVM-Host /usr/local/bin/DMRGateway /usr/local/bin/YSFGateway /usr/local/bin/P25Gateway /usr/local/bin/NXDNGateway /usr/local/bin/ircddbgatewayd 2>/dev/null || true
if [ -f /etc/mmdvm/fw-url ]; then tr -d '\r\n' < /etc/mmdvm/fw-url > /etc/mmdvm/fw-url.tmp && mv /etc/mmdvm/fw-url.tmp /etc/mmdvm/fw-url; fi
[ -f /etc/mmdvm/bm-masters.json ] || printf '[]\n' > /etc/mmdvm/bm-masters.json
[ -f /usr/local/etc/DMRIds.dat ] || : > /usr/local/etc/DMRIds.dat
[ -f /usr/local/etc/nextionUsers.csv ] || : > /usr/local/etc/nextionUsers.csv
[ -f /usr/local/etc/NXDN.csv ] || : > /usr/local/etc/NXDN.csv
# [REVERT 1.0.92] 1.0.91 曾把 P25 的 HostsFile1/2 都改成同一个文本文件，
# 实测会让 P25Gateway 报 "Unable to load/parse file"、46002/46020 都挂不上。
# 本版撤销该改动，P25 hosts 维持原状（46002 可链路），46020 另行排查。
# [FIX 1.0.87] NXDNGateway 读的是 /usr/local/etc/NXDNHosts.txt（带下划线的 NXDN_Hosts.txt
# 是另一个名字），缺了它就报 "Unable to open the Hosts file" 且反射器地址表为空，
# 表现为 NXDN 联网但通不了。这里做成软链接，数据更新后自动跟随。
if [ -f /usr/local/etc/hosts/NXDN_Hosts.txt ]; then
  ln -sfn /usr/local/etc/hosts/NXDN_Hosts.txt /usr/local/etc/NXDNHosts.txt
fi
# [FIX 1.0.87] D-STAR：只要配了反射器，就必须"开机自动链接 + 掉线自动重连"，
# 否则 atStartup1=0 时热点永远连不上反射器（D-STAR 收发全不通）。
if [ -f /etc/ircddbgateway ] && grep -qE "^reflector1=.+" /etc/ircddbgateway; then
  sed -i -e "s/^atStartup1=0[[:space:]]*$/atStartup1=1/" -e "s/^reconnect1=0[[:space:]]*$/reconnect1=1/" /etc/ircddbgateway
fi
[ -f /opt/mmdvm-web/data/radioid_cache.json ] || printf '{}\n' > /opt/mmdvm-web/data/radioid_cache.json
/usr/local/libexec/hosts-build.py >/dev/null 2>&1 || true
# [FIX 1.0.97] 语音提示文件：/usr/local/etc/Audio 一直是空的，而 P25/NXDN/D-STAR 网关
# 都要读 <Language>.indx（缺了就报 Unable to open the index file - .../en_GB.indx）。
# 上游源码里自带这些数据（/opt/src-mmdvm/ircDDBGateway/Data），这里补齐（幂等、不覆盖已有）。
install -d -m 0755 /usr/local/etc/Audio
if [ -d /opt/src-mmdvm/ircDDBGateway/Data ]; then
  cp -an /opt/src-mmdvm/ircDDBGateway/Data/. /usr/local/etc/Audio/ 2>/dev/null || true
fi
# [FIX 1.0.100] P25 用的是 IMBE 提示音（.imbe），和 ircDDB 的 .ambe 不是一套。
# 上游 P25Clients/P25Gateway/Audio 里自带 .imbe + .indx，缺了会报
# "Unable to stat the IMBE file - en_GB.imbe"。一并补齐（幂等）。
if [ -d /opt/src-mmdvm/P25Clients/P25Gateway/Audio ]; then
  cp -an /opt/src-mmdvm/P25Clients/P25Gateway/Audio/. /usr/local/etc/Audio/ 2>/dev/null || true
fi
# [FIX 1.0.96] 这一行 hosts-build.py 会把 /usr/local/etc/P25Hosts.json 覆盖成旧格式
# （只有 1 条、name 是中文标签）。本机这版 P25Gateway 把 JSON 的 name 字段当作要
# 【逐条 DNS 解析】的地址 → 于是出现 Unable to resolve、46020 永远找不到。
# 必须放在 hosts-build.py 之后再重建：只收录地址本身是 IP 的条目 + 强制带 46002/46020，
# name 直接写 IP（无需 DNS）→ 秒加载、两条静态反射器都能挂上。
python3 - <<'PYGEN' || true
import io, json, re
rows = {}
try:
    for ln in io.open('/usr/local/etc/hosts/P25_Hosts.txt', encoding='utf-8', errors='replace'):
        s = ln.strip()
        if not s or s.startswith('#'): continue
        f = s.split()
        if len(f) < 3 or not f[0].isdigit(): continue
        if not re.match(r'^[0-9]+(\.[0-9]+){3}$', f[1]): continue
        try: port = int(f[2])
        except Exception: continue
        rows[int(f[0])] = {'designator': int(f[0]), 'name': f[1], 'port': port, 'ipv4': f[1], 'ipv6': None}
except Exception:
    pass
rows[46002] = {'designator': 46002, 'name': '39.96.117.224', 'port': 41000, 'ipv4': '39.96.117.224', 'ipv6': None}
rows[46020] = {'designator': 46020, 'name': '8.163.29.23', 'port': 41000, 'ipv4': '8.163.29.23', 'ipv6': None}
io.open('/usr/local/etc/P25Hosts.json', 'w', encoding='utf-8').write(json.dumps({'reflectors': [rows[k] for k in sorted(rows)]}, ensure_ascii=False, indent=2))
io.open('/usr/local/etc/private/P25Hosts.json', 'w', encoding='utf-8').write(json.dumps({'reflectors': []}, ensure_ascii=False, indent=2))
PYGEN
/usr/local/libexec/channel-setup.py apply "$(cat /etc/mmdvm/channels.json)" || echo "警告：通道配置应用失败（已由 EXIT trap 保证服务被拉起）"
# [1.0.129] install factory-default snapshot + one-click factory reset script
if [ -f "$ROOT/usr/local/share/arch/factory-defaults.tar.gz" ]; then
  install -d -m 0755 /var/lib/arch/factory-defaults
  rm -rf /var/lib/arch/factory-defaults/* 2>/dev/null || true
  tar -xzf "$ROOT/usr/local/share/arch/factory-defaults.tar.gz" -C /var/lib/arch/factory-defaults
  touch /var/lib/arch/factory-defaults/.ok
fi
[ -f "$ROOT/usr/local/bin/arch-factory-reset.sh" ] && install -m 0755 "$ROOT/usr/local/bin/arch-factory-reset.sh" /usr/local/bin/arch-factory-reset.sh
install -m 0755 "$ROOT/arch-hotspot-update" /usr/local/bin/arch-hotspot-update
cat > /etc/arch-hotspot-release <<EOF
CHANNEL=$CHANNEL
VERSION=$VERSION
UPDATE_BASE=https://hljrsa.cn:50113/releases/ota/arch-hotspot
EOF
# ARCH 1.0.141: 修掉 [brew].username 非法（例如空串 ""）导致 Bluestation 拒绝整份
# config.toml、TDR Link 切过去后基站起不来的问题（已装好的设备在这里做一次兜底）。
if [ -x /usr/local/libexec/tetra-brew-fix.py ]; then
  python3 /usr/local/libexec/tetra-brew-fix.py || true
fi
# [1.0.143] 安装脚本不再无条件让常规模式自启：装完按当前模式对齐一次，
# 并启用开机模式守卫，避免 MMDVM 与 TETRA 同时启动抢 SX1255。
if [ -f "$ROOT/usr/local/bin/arch-mode-guard.sh" ]; then
  install -m 0755 "$ROOT/usr/local/bin/arch-mode-guard.sh" /usr/local/bin/arch-mode-guard.sh
fi
if [ -f "$ROOT/etc/systemd/system/arch-mode-guard.service" ]; then
  install -m 0644 "$ROOT/etc/systemd/system/arch-mode-guard.service" /etc/systemd/system/arch-mode-guard.service
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl enable arch-mode-guard.service >/dev/null 2>&1 || true
  bash /usr/local/bin/arch-mode-guard.sh >/dev/null 2>&1 || true
fi
# [1.0.144] 供电/降频自检：欠压会让 SDR 发射丢块，提前告警
if [ -f "$ROOT/usr/local/bin/arch-power-check.sh" ]; then
  install -m 0755 "$ROOT/usr/local/bin/arch-power-check.sh" /usr/local/bin/arch-power-check.sh
  install -m 0644 "$ROOT/etc/systemd/system/arch-power-check.service" /etc/systemd/system/arch-power-check.service 2>/dev/null || true
  install -m 0644 "$ROOT/etc/systemd/system/arch-power-check.timer" /etc/systemd/system/arch-power-check.timer 2>/dev/null || true
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl enable --now arch-power-check.timer >/dev/null 2>&1 || true
  bash /usr/local/bin/arch-power-check.sh >/dev/null 2>&1 || true
fi
if [ "$CHANNEL" = "public" ]; then systemctl disable --now tetra-bs.service 2>/dev/null || true; fi
cat > /etc/sudoers.d/arch-web <<'SUDO'
Defaults:mmdvm-web !requiretty
mmdvm-web ALL=(root) NOPASSWD: /usr/local/bin/web-manage.sh *, /usr/local/bin/web-fmo.sh *, /usr/local/bin/web-multi.sh *, /usr/local/bin/sys-manage.sh *, /usr/local/bin/web-tetra.sh *, /usr/local/libexec/mmdvm-save.py *, /usr/local/libexec/hosts-build.py *
SUDO
chmod 0440 /etc/sudoers.d/arch-web
visudo -cf /etc/sudoers.d/arch-web >/dev/null
systemctl daemon-reload
udevadm control --reload-rules >/dev/null 2>&1 || true
systemctl enable --now arch-usb-net.service arch-net-priority.timer >/dev/null 2>&1 || true
systemctl enable arch-hotspot-heal.service >/dev/null 2>&1 || true
# [1.0.153] 远程维护脚本必须【每次升级都从包里更新】。
# 以前这里只有 chmod、从来没有 install —— 修好的 agent/看门狗只能靠刷镜像才生效，
# 实测会造成"设备在后台显示在线、但远程就是连不进去"，而且升级也修不好。
for _rf in /usr/local/libexec/arch-remote-agentd.py /usr/local/bin/arch-remote-tunnel.sh \
           /usr/local/bin/arch-remote-watchdog.sh /usr/local/bin/arch-remote-debug-ctl \
           /usr/local/bin/arch-device-id /usr/local/libexec/arch-remote-debug.py; do
  if [ -f "$ROOT$_rf" ]; then
    install -m 0755 "$ROOT$_rf" "$_rf"
  fi
done
chmod 0755 /usr/local/libexec/arch-remote-agentd.py /usr/local/bin/arch-remote-tunnel.sh /usr/local/bin/arch-remote-debug-ctl /usr/local/bin/arch-device-id 2>/dev/null || true
# Per-device secret for the local remote-debug agent. Never ship a shared secret.
SECRET_FILE=/etc/mmdvm/remote-debug.secret
if [ ! -s "$SECRET_FILE" ]; then
  umask 077
  od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$SECRET_FILE"
  chmod 0600 "$SECRET_FILE"
fi
[ -f /etc/mmdvm/remote-debug.enable ] || echo 1 > /etc/mmdvm/remote-debug.enable
install -m 0600 "$ROOT/etc/mmdvm/arch-tunnel.key" /etc/mmdvm/arch-tunnel.key 2>/dev/null || true
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable --now arch-remote-agent.service arch-remote-tunnel.service >/dev/null 2>&1 || true
chmod 0755 /usr/local/libexec/arch-remote-debug.py /usr/local/bin/arch-remote-debug-ctl 2>/dev/null || true
[ -f /etc/mmdvm/remote-debug.enable ] || echo 1 > /etc/mmdvm/remote-debug.enable
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable --now arch-remote-debug.timer >/dev/null 2>&1 || true
# [FIX 1.0.107] 生成独立设备ID
/usr/local/bin/arch-device-id >/dev/null 2>&1 || true
# [1.0.147] 原来这里立刻 restart 远程管理服务。那句 restart 重启的正是承载"远程调试会话"的
# 反向隧道 —— 放在中段会把【正在跑这次安装的那个人】连同安装进程一起掐死：日志停在
# "arch: config.toml [brew] ok"、末尾一个 Terminated、永远不打印"安装完成"（实测两次）。
# 现在这里只保证服务在运行，真正的重启动挪到脚本最末尾、用独立 systemd 单元延时执行。
systemctl enable --now arch-remote-agent.service arch-remote-tunnel.service arch-remote-debug.timer >/dev/null 2>&1 || true
systemctl start arch-remote-debug.service >/dev/null 2>&1 || true
systemctl enable --now mosquitto nginx mmdvm-web.service >/dev/null 2>&1 || true
SX1255_PRESENT=0
if /usr/local/bin/arch-sx-probe; then
  SX1255_PRESENT=1
  unit_up mmdvm-multi.service
elif [ -f /etc/mmdvm/soapy-sx-verified ] || [ -f /etc/mmdvm/arch-build-version ]; then
  # The device has worked before. Keep the units enabled and let the boot-time
  # self-heal retry the probe instead of permanently disabling the hotspot.
  SX1255_PRESENT=1
  echo "警告：本次 SX1255 探测失败，保留热点服务并等待自愈"
  systemctl enable mmdvm-multi.service >/dev/null 2>&1 || true
else
  systemctl disable --now mmdvm-multi.service 2>/dev/null || true
  for i in $(seq 1 12); do
    systemctl disable --now "mmdvm-iq@$i.service" "mmdvmhost@$i.service" 2>/dev/null || true
  done
fi
systemctl enable --now arch-data-bootstrap.timer >/dev/null 2>&1 || true
systemctl start --no-block arch-data-bootstrap.service 2>/dev/null || true
systemctl reload nginx
DMR_ID="$(awk -F= '/^[[:space:]]*Id=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' /etc/DMRGateway.ini 2>/dev/null || true)"
case "$DMR_ID" in
  *[1-9]*) touch /etc/mmdvm/dmr-ready; unit_up dmrgateway.service ;;
  *) rm -f /etc/mmdvm/dmr-ready; systemctl disable --now dmrgateway.service 2>/dev/null || true ;;
esac
# [1.0.159] 多 DMR 通道：通道 2 起建立独立 DMRGateway 配置与端口
python3 - <<'PY'
# [1.0.161] DMR multi config editor: line based, preserve repeated PassAllTG/PassAllPC keys
import json, os, shutil

def set_key(lines, section, key, value):
    header = '[' + section.lower() + ']'
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == header:
            start = i + 1
            break
    if start is None:
        lines.append('\n[' + section + ']\n')
        start = len(lines)
    end = start
    while end < len(lines):
        s = lines[end].strip()
        if s.startswith('[') and s.endswith(']'):
            break
        end += 1
    target = key.lower() + '='
    for i in range(start, end):
        if lines[i].strip().lower().startswith(target):
            lines[i] = key + '=' + str(value) + '\n'
            return
    lines.insert(end, key + '=' + str(value) + '\n')

def edit(path, changes):
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    for sec, key, val in changes:
        set_key(lines, sec, key, val)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    os.replace(tmp, path)


def ensure_password(path):
    # [1.0.161-pw] DMRGateway 不允许空密码；免密服务端使用占位值 x。
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    header = '[dmr network 1]'
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == header:
            start = i + 1
            break
    if start is None:
        lines.extend(['\n[DMR Network 1]\n', 'Password=x\n'])
    else:
        end = start
        while end < len(lines):
            s = lines[end].strip()
            if s.startswith('[') and s.endswith(']'):
                break
            end += 1
        found = False
        for i in range(start, end):
            s = lines[i].strip()
            if s.lower().startswith('password='):
                found = True
                if s.split('=', 1)[1].strip() == '':
                    lines[i] = 'Password=x\n'
                break
        if not found:
            lines.insert(end, 'Password=x\n')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    os.replace(tmp, path)


ensure_password('/etc/DMRGateway.ini')

try:
    plan = json.load(open('/etc/mmdvm/channels.json'))
except Exception:
    plan = {}
dmr = [int(x.get('n') or 0) for x in plan.get('list', []) if str(x.get('mode', '')).upper() == 'DMR']
for n in dmr:
    if n <= 1:
        continue
    path = '/etc/DMRGateway-%d.ini' % n
    if not os.path.exists(path):
        try:
            shutil.copy2('/etc/DMRGateway.ini', path)
        except Exception:
            continue
    edit(path, [('General', 'LocalPort', str(62031 + (n - 1) * 10)),
                ('General', 'RptPort', str(62032 + (n - 1) * 10)),
                ('MQTT', 'Name', 'dmr-gateway-%d' % n)])
    ensure_password(path)
    hp = '/etc/MMDVM-Host-%d.ini' % n
    edit(hp, [('DMR Network', 'GatewayPort', str(62031 + (n - 1) * 10)),
              ('DMR Network', 'LocalPort', str(62032 + (n - 1) * 10))])
PY
for _n in $(python3 -c 'import json;print(" ".join(str(int(x.get("n"))) for x in json.load(open("/etc/mmdvm/channels.json")).get("list",[]) if str(x.get("mode","")).upper()=="DMR" and int(x.get("n",0))>1))' 2>/dev/null); do
  systemctl enable "dmrgateway@$_n.service" >/dev/null 2>&1 || true
  unit_up "dmrgateway@$_n.service"
done
CHANNELS=$(python3 -c 'import json;print(int(json.load(open("/etc/mmdvm/channels.json")).get("channels",6)))' 2>/dev/null || echo 6)
if [ "$SX1255_PRESENT" -eq 1 ]; then
  for i in $(seq 1 "$CHANNELS"); do
    [ -f "/etc/MMDVM-IQ-$i.ini" ] && unit_up "mmdvm-iq@$i.service" || true
    [ -f "/etc/MMDVM-Host-$i.ini" ] && unit_up "mmdvmhost@$i.service" || true
  done
fi
install -m 0755 "$ROOT/usr/local/bin/MMDVM-Multi" /usr/local/bin/MMDVM-Multi
python3 /usr/local/libexec/arch-fm-audio-migrate.py || true
systemctl daemon-reload
# [FIX 1.0.93] 原来用 try-restart：它只对【已在运行】的单元有效。
# 升级过程中 mmdvm-multi/各通道早已被停掉，try-restart 什么都不做，
# 结果升级完成后射频引擎与 DMR 网关一直 inactive（实测发生过）。
# 改为 enable --now，确保升级结束后一定要在运行。
unit_up mmdvm-multi.service
sleep 2
CHANNELS_MIG=$(python3 -c 'import json;print(int(json.load(open("/etc/mmdvm/channels.json")).get("channels",6)))' 2>/dev/null || echo 6)
for i in $(seq 1 "$CHANNELS_MIG"); do
  unit_up "mmdvm-iq@$i.service"
  unit_up "mmdvmhost@$i.service"
  if [ "$MMDVM_START" -eq 1 ]; then
    systemctl restart "mmdvm-iq@$i.service" 2>/dev/null || true
    systemctl restart "mmdvmhost@$i.service" 2>/dev/null || true
  fi
done
# 有 DMR Id 就把 DMRGateway 也拉起来（原来只 enable 不 start）
if grep -qE '^Id=[0-9]*[1-9]' /etc/DMRGateway.ini 2>/dev/null; then
  unit_up dmrgateway.service
  for _n in $(python3 -c 'import json;print(" ".join(str(int(x.get("n"))) for x in json.load(open("/etc/mmdvm/channels.json")).get("list",[]) if str(x.get("mode","")).upper()=="DMR" and int(x.get("n",0))>1))' 2>/dev/null); do
    unit_up "dmrgateway@$_n.service"
  done
fi
# === 引擎健康检查：跑不起来就自动回退升级前的引擎 ===
# [1.0.147] TETRA 模式下【必须跳过】：此时 mmdvm-multi 本来就不该运行，自检必然
# "发现它没在跑"→ 误报"新版 MMDVM-Multi 未能运行"→ 回退引擎并重启 MMDVM，
# 既白白回退，又与 TETRA 基站抢 SX1255（实测：升级后用户第一次切换 100% 失败一次）。
if [ "$MMDVM_START" -eq 0 ]; then
  echo "当前模式=TETRA：跳过常规射频引擎自检（MMDVM 本就不该运行）"
fi
if [ "${SX1255_PRESENT:-0}" -eq 1 ] && [ "$MMDVM_START" -eq 1 ]; then
  sleep 3
  if ! systemctl is-active --quiet mmdvm-multi.service; then
    echo "警告：新版 MMDVM-Multi 未能运行，回退到升级前的引擎"
    for ef in MMDVM-Multi MMDVM-IQ; do
      [ -f "$ENGINE_BAK/$ef" ] && install -m 0755 "$ENGINE_BAK/$ef" "/usr/local/bin/$ef" 2>/dev/null || true
    done
    systemctl restart mmdvm-multi.service 2>/dev/null || true
    sleep 2
    systemctl is-active --quiet mmdvm-multi.service && echo "已回退旧引擎，服务恢复" || echo "警告：回退后仍未运行"
  fi
  echo -n "当前引擎 MD5: "; md5sum /usr/local/bin/MMDVM-Multi 2>/dev/null | awk '{print $1}'
fi

sync_ircddb_hosts
configure_live_events
restart_live_event_hosts
systemctl daemon-reload
systemctl enable --now arch-qso-live.service arch-gateway-status.service arch-gateway-autoconnect.service >/dev/null 2>&1 || true
python3 /usr/local/libexec/arch-gateway-autoconnect.py || true
# [FIX 1.0.99] 远程维护看门狗必须【显式】启用：放在各单元已复制 + daemon-reload 之后，
# 否则（实测）安装完 arch-remote-watchdog.timer 仍是 disabled/inactive，
# 一旦 agent 卡死就没有自愈，远程维护会彻底失联。
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable --now arch-remote-watchdog.timer >/dev/null 2>&1 || true
systemctl start arch-remote-watchdog.service >/dev/null 2>&1 || true
# [FIX 1.0.103] 应急热点（ARCH多模网关）：4G 网卡在线，或有线/WiFi 均断开超过 30 秒时，
# 自动开一个【无密码】Wi-Fi 热点，方便用户在没法访问局域网时连上来配置设备。
# 热点跑在虚拟接口 uap0 上（不动 wlan0），所以设备自己连的 WiFi 不受影响；
# ipv4.method=shared 自带 DHCP+NAT，4G 在线时连上热点还能上网。
install -m 0755 "$ROOT/usr/local/bin/arch-ap-fallback.sh" /usr/local/bin/arch-ap-fallback.sh
if command -v nmcli >/dev/null 2>&1; then
  if ! nmcli -t -f NAME connection show 2>/dev/null | grep -qx arch-ap; then
    nmcli connection add type wifi ifname uap0 con-name arch-ap ssid ARCH多模网关 autoconnect no save yes >/dev/null 2>&1 || true
  fi
  nmcli connection modify arch-ap 802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 >/dev/null 2>&1 || true
  nmcli connection modify arch-ap ipv4.method shared ipv6.method disabled >/dev/null 2>&1 || true
  nmcli connection modify arch-ap connection.autoconnect no >/dev/null 2>&1 || true
fi
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable --now arch-ap-fallback.timer >/dev/null 2>&1 || true
systemctl restart mmdvm-web.service 2>/dev/null || true
systemctl restart fmo-bridge.service 2>/dev/null || true
systemctl restart ircddbgatewayd.service 2>/dev/null || true
# [FIX 1.0.104] 应急热点看门狗必须【显式】启用：实测上一版装完 timer 仍是 disabled，
# 这样重启后热点不会自动按规则开关。放在安装脚本最后、各单元都已就位后再启用一次。
systemctl daemon-reload >/dev/null 2>&1 || true
for _u in arch-ap-fallback.timer arch-remote-watchdog.timer arch-qso-live.service; do
  systemctl enable --now "$_u" >/dev/null 2>&1 || true
done
systemctl start arch-ap-fallback.service >/dev/null 2>&1 || true
# [FIX 1.0.110] ???? WiFi ????????????? rfkill ???????
command -v rfkill >/dev/null 2>&1 && rfkill unblock wifi >/dev/null 2>&1 || true
command -v nmcli >/dev/null 2>&1 && nmcli radio wifi on >/dev/null 2>&1 || true

# [FIX 1.0.134] FINAL enforcement (install must never abort on a service start):
# enable first (this is what actually matters for the next boot), then start best-effort.
systemctl daemon-reload >/dev/null 2>&1 || true
for _u in arch-qso-live.service arch-gateway-status.service arch-gateway-autoconnect.service \
          arch-ap-fallback.timer arch-remote-watchdog.timer arch-net-priority.timer \
          arch-remote-agent.service arch-remote-tunnel.service arch-remote-debug.timer \
          mmdvm-multi.service mmdvm-web.service nginx.service mosquitto.service; do
  systemctl enable "$_u" >/dev/null 2>&1 || true
done
for _u in arch-qso-live.service arch-gateway-status.service arch-gateway-autoconnect.service \
          arch-ap-fallback.timer arch-remote-watchdog.timer arch-remote-agent.service \
          arch-remote-tunnel.service arch-remote-debug.timer mmdvm-multi.service \
          mmdvm-web.service nginx.service mosquitto.service; do
  # [1.0.147] TETRA 模式下不启动常规射频（开机自启已置位，模式守卫负责对齐）
  if [ "$_u" = "mmdvm-multi.service" ] && [ "$MMDVM_START" -eq 0 ]; then continue; fi
  systemctl start "$_u" >/dev/null 2>&1 || true
done
echo -n "关键服务状态: "
for _u in arch-qso-live arch-gateway-status arch-gateway-autoconnect arch-ap-fallback.timer arch-remote-tunnel mmdvm-multi mmdvm-web; do
  printf "%s=%s " "$_u" "$(systemctl is-enabled "$_u" 2>/dev/null)"
done
echo

ensure_services_up
echo "ARCH 热点 $CHANNEL $VERSION 安装完成"
echo "管理页面：http://$(hostname -I | awk '{print $1}')/"
# [1.0.153] IP 会随 DHCP 变，给出一个不变的 mDNS 名字，免得用户"找不到设备"
echo "备用地址：http://$(hostname).local/   （同一局域网、支持 mDNS 的设备可用；IP 变了也能进）"

# [1.0.145] 安装收尾：上面 ensure_services_up / 最终 enforce 会把常规模式与瘦身组件
# 无条件拉起。若设备当前选的是 TETRA 模式，必须在最后再跑一次模式守卫把状态对齐，
# 否则装完就会出现 MMDVM 与 TETRA 抢同一块 SX1255、且瘦身失效。
bash /usr/local/bin/arch-mode-guard.sh >/dev/null 2>&1 || true

# [1.0.152] TETRA 模式：装完在"系统已经安静"之后再干净重启一次基站。
#   原因（实测 BG2CPU/Pi3B）：安装中途 mode-guard 拉起的基站启动瞬间就丢样本，
#   叠加安装收尾的 CPU 抖动后，基站会进入永久的 TX 迟到状态（Too late ~300 条/秒，
#   手台注册不上 —— 就是"切到 TETRA 没反应"），手动重启一次立即恢复 0 丢块。
if [ "$CUR_MODE" = "tetra" ] && systemctl is-active --quiet tetra-bs.service; then
  echo "TETRA 模式：安装收尾干净重启一次基站（避免安装期间的初始化抖动留下坏状态）"
  systemctl restart tetra-bs.service 2>/dev/null || true
  sleep 2
fi

# [1.0.146] TETRA 采样率纠正：SX1255 跑 TETRA 必须 600 kHz。
# 老版本被写成了 150 kHz（从 MMDVM 引擎那边照搬过来的），会让基站发射块全部迟到、
# 手台注册不上（表现为"切到 TDR Link 没用"）。这里升级时强制纠正成 600000。
if [ -f /opt/tetra/ui-config.json ]; then
  python3 - <<'PY' || true
import json, io, shutil, time
p = '/opt/tetra/ui-config.json'
try:
    d = json.load(io.open(p, encoding='utf-8'))
except Exception:
    raise SystemExit(0)
ch = False
def fix(o):
    global ch
    if isinstance(o, dict):
        for k, v in list(o.items()):
            if k == 'sample_rate' and v != 600000:
                o[k] = 600000; ch = True
            elif isinstance(v, (dict, list)):
                fix(v)
    elif isinstance(o, list):
        for v in o:
            fix(v)
fix(d)
if ch:
    shutil.copy2(p, p + '.bak-146-' + time.strftime('%Y%m%d-%H%M%S'))
    io.open(p, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=2) + '\n')
    print('TETRA sample_rate -> 600000 (ui-config.json)')
PY
fi
if [ -f /opt/tetra/config.toml ] && grep -q '^sample_rate *= *150000' /opt/tetra/config.toml; then
  cp -a /opt/tetra/config.toml "/opt/tetra/config.toml.bak-146-$(date +%Y%m%d-%H%M%S)"
  sed -i 's/^sample_rate *= *150000/sample_rate = 600000/' /opt/tetra/config.toml
  echo "TETRA sample_rate -> 600000 (config.toml)"
fi


# [1.0.161] TETRA GSSI 10 must be allowed on Brew for all registered users.
# Existing hotspots may still have local_ssi_ranges = [[0,90]], which turns
# GSSI 10 into a local-only group and makes FlowStation drop GROUP_TX 10
# and refuse to forward uplink group 10 to Brew. Split any range containing 10
# into ranges that exclude 10; leave all other ranges untouched.
if [ -f /opt/tetra/config.toml ]; then
  python3 - <<'PY'
import re, shutil, time
from pathlib import Path
p = Path('/opt/tetra/config.toml')
s = p.read_text(encoding='utf-8')
m = re.search(r'(?ms)^local_ssi_ranges\s*=\s*\[(.*?)^\]\s*$', s)
if m:
    ranges = [(int(a), int(b)) for a, b in re.findall(r'\[\s*(\d+)\s*,\s*(\d+)\s*\]', m.group(1))]
    out = []
    for a, b in ranges:
        if a <= 10 <= b:
            if a <= 9:
                out.append((a, 9))
            if 11 <= b:
                out.append((11, b))
        else:
            out.append((a, b))
    if out != ranges:
        body = '\n'.join('    [%d, %d],' % r for r in out)
        new = 'local_ssi_ranges = [\n' + body + '\n]'
        shutil.copy2(p, str(p) + '.bak-gssi10-' + time.strftime('%Y%m%d-%H%M%S'))
        p.write_text(s[:m.start()] + new + s[m.end():], encoding='utf-8')
        print('TETRA local_ssi_ranges: GSSI 10 moved out of local-only')
PY
fi



# [1.0.148] 安装收尾：解除升级窗口标记（看门狗恢复正常判据）
rm -f /run/arch-upgrade.lock 2>/dev/null || true

# [1.0.147] 远程管理服务重启动 —— 挪到整支脚本的【最后】，并且脱离当前会话执行。
#   原因：这条 restart 会重启反向隧道，而"跑安装的人"很可能正通过它连进来（远程调试/后台下发）。
#   放在脚本中段 = 把安装进程自己掐死（实测两次：停在 arch: config.toml [brew] ok + Terminated，
#   日志永远不打印"安装完成"，用户以为升级失败）。
#   现在：等安装全部完成、日志打完"安装完成"之后，由 systemd 在 5 秒后于【独立 cgroup】里执行，
#   无论这次安装是本地 ssh、网页升级还是远程调试下发进来的，都不会被自己打断。
if command -v systemd-run >/dev/null 2>&1; then
  if systemd-run --collect --on-active=5s /bin/sh -c \
       'systemctl try-restart arch-remote-debug.timer arch-remote-tunnel.service arch-remote-agent.service' \
       >/dev/null 2>&1; then
    echo "远程管理服务将在 5 秒后于独立单元中重启动（不影响本次安装）"
  else
    systemctl try-restart arch-remote-agent.service arch-remote-tunnel.service arch-remote-debug.timer >/dev/null 2>&1 || true
  fi
else
  systemctl try-restart arch-remote-agent.service arch-remote-tunnel.service arch-remote-debug.timer >/dev/null 2>&1 || true
fi

# [1.0.165] all post-install migrations complete; updater checks this marker
printf '%s\n' "$VERSION" > /etc/mmdvm/arch-install-complete
