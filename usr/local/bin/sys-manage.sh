#!/bin/bash
# sys-manage.sh —— Web 控制台「系统设置」的唯一特权入口（mmdvm-web 通过 sudo -n 调用）
set -u
ACT="${1:-}"; shift || true

# ==================== WiFi 辅助（NetworkManager / wpa_supplicant 双后端）====================
# 兼容：树莓派 3（多为 wpa_supplicant + dhcpcd）与树莓派 4/5（NetworkManager）
_wifi_iface() {
    for d in /sys/class/net/*/wireless; do
        [ -e "$d" ] || continue
        basename "$(dirname "$d")"; return 0
    done
    iw dev 2>/dev/null | awk '/Interface/{print $2; exit}'
}
_wifi_backend() {
    if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager 2>/dev/null; then
        echo nm; return
    fi
    if command -v wpa_cli >/dev/null 2>&1; then
        if systemctl is-active --quiet wpa_supplicant 2>/dev/null || pgrep -x wpa_supplicant >/dev/null 2>&1; then
            echo wpa; return
        fi
        echo wpa-off; return
    fi
    echo none
}
_wifi_band() {
    f="${1:-0}"
    case "$f" in ''|*[!0-9]*) echo "-"; return ;; esac
    if [ "$f" -ge 2400 ] && [ "$f" -le 2500 ]; then echo "2.4G"
    elif [ "$f" -ge 4900 ]; then echo "5G"
    else echo "-"; fi
}
_wifi_radio_state() {
    if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager 2>/dev/null; then
        s=$(nmcli radio wifi 2>/dev/null | tr 'A-Z' 'a-z')
        [ -n "$s" ] && { echo "$s"; return; }
    fi
    if rfkill list wifi 2>/dev/null | grep -q "Soft blocked: yes"; then echo disabled; else echo enabled; fi
}
_wifi_radio_on() {
    if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager 2>/dev/null; then
        [ "$(nmcli radio wifi 2>/dev/null | tr 'A-Z' 'a-z')" = "disabled" ] && nmcli radio wifi on >/dev/null 2>&1
    fi
    rfkill unblock wifi 2>/dev/null || true
    sleep 1
}
_wpa_conf() { echo "/etc/wpa_supplicant/wpa_supplicant.conf"; }
_wifi_saved_count() {
    if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager 2>/dev/null; then
        nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="802-11-wireless"' | wc -l
    else
        grep -c 'network=' "$(_wpa_conf)" 2>/dev/null || echo 0
    fi
}
_nm_terse() {
    # nmcli 的 terse 输出用冒号分隔，且把值里的冒号转义成 \:
    # 本函数统一转成 | 分隔（先保护 \: ，再转，再还原），兼容不支持
    # --separator 的 nmcli 构建（Debian 的 nmcli 1.52 就没有该选项）
    nmcli -t "$@" 2>/dev/null | sed 's/\\:/__C__/g' | tr ':' '|' | sed 's/__C__/:/g'
}
_wifi_conn_of_ssid() {   # $1=SSID -> nm 连接名
    nmcli -t -f NAME,802-11-wireless.ssid connection show 2>/dev/null \
      | awk -F: -v s="$1" '$2==s{print $1; exit}'
}

# ==================== WiFi 凭据备份 / 开机自愈 ====================
# 备份文件：/etc/wpa_supplicant/wpa_supplicant.conf（含 country=CN），
# 一旦 NM 配置丢失（本项目遇到过），wifi-restore 可据此重建，避免"开机不自动连"。
_wpa_backup_add() {   # $1=SSID $2=密码(可空) $3=隐藏(0/1)
    local conf=/etc/wpa_supplicant/wpa_supplicant.conf ssid="$1" pw="$2" hid="${3:-0}"
    [ -n "$ssid" ] || return 0
    mkdir -p /etc/wpa_supplicant
    [ -f "$conf" ] || printf 'ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1\ncountry=CN\n' > "$conf"
    python3 - "$conf" "$ssid" "$pw" "$hid" <<'PYWPA'
import re, sys
conf, ssid, pw, hid = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    s = open(conf, encoding="utf-8", errors="ignore").read()
except Exception:
    s = "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1\ncountry=CN\n"
if "country=" not in s:
    s = "country=CN\n" + s
blocks = re.findall(r"network=\{[^}]*\}", s, re.S)
keep = []
for b in blocks:
    m = re.search(r'ssid="([^"]*)"', b)
    if m and m.group(1) == ssid:
        continue
    keep.append(b)
rest = re.sub(r"network=\{[^}]*\}", "", s, flags=re.S).strip()
blk = ['network={', '\tssid="%s"' % ssid]
if pw:
    blk.append('\tpsk="%s"' % pw)
else:
    blk.append('\tkey_mgmt=NONE')
if hid == "1":
    blk.append('\tscan_ssid=1')
blk.append('}')
out = rest + "\n" + "\n".join(keep + ["\n".join(blk)]) + "\n"
open(conf, "w", encoding="utf-8").write(out)
print("备份凭据: %s" % ssid)
PYWPA
    chmod 600 "$conf" 2>/dev/null || true
}
_wpa_backup_list() {   # 输出 "SSID<TAB>密码"（只读，供恢复用）
    python3 - <<'PYLIST'
import re
try:
    s = open("/etc/wpa_supplicant/wpa_supplicant.conf", encoding="utf-8", errors="ignore").read()
except Exception:
    raise SystemExit(0)
for b in re.findall(r"network=\{[^}]*\}", s, re.S):
    sid = re.search(r'ssid="([^"]*)"', b)
    psk = re.search(r'psk="([^"]*)"', b)
    if sid and sid.group(1):
        print("%s\t%s" % (sid.group(1), psk.group(1) if psk else ""))
PYLIST
}
_prefix2mask() {   # $1=前缀长度 -> 点分掩码
    python3 - "$1" <<'PYM'
import sys
try:
    p = int(sys.argv[1])
    m = (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF if p > 0 else 0
    print("%d.%d.%d.%d" % ((m >> 24) & 255, (m >> 16) & 255, (m >> 8) & 255, m & 255))
except Exception:
    print("")
PYM
}
_mask2prefix() {   # $1=点分掩码或前缀 -> 前缀长度
    python3 - "$1" <<'PYM'
import sys
a = sys.argv[1].strip()
try:
    if "." not in a:
        print(int(a)); raise SystemExit(0)
    m = 0
    for x in a.split("."):
        m = (m << 8) | (int(x) & 255)
    print(bin(m).count("1"))
except Exception:
    print("")
PYM
}
_wifi_fix_profile() {   # $1=连接名（规范化：自动连接 / 无限重试 / 优先级）
    local cn="$1"
    [ -n "$cn" ] || return 0
    nmcli connection modify "$cn" connection.autoconnect yes >/dev/null 2>&1 || true
    nmcli connection modify "$cn" connection.autoconnect-priority 10 >/dev/null 2>&1 || true
    nmcli connection modify "$cn" connection.autoconnect-retries 0 >/dev/null 2>&1 || true
    nmcli connection modify "$cn" ipv4.may-fail no >/dev/null 2>&1 || true
    nmcli connection modify "$cn" ipv6.may-fail no >/dev/null 2>&1 || true
}

# ==================== 后台任务（系统更新 / 固件更新）====================
# 用 systemd-run 起临时单元，Web 请求立刻返回；进度写日志文件
JOB_LOG_DIR=/var/log
FW_URL_FILE=/etc/mmdvm/fw-url
FW_DEFAULT_URL="https://arch.hljrsa.cn/downloads/firmware/mmdvm-multi.json"

_fw_url() {
    if [ -s "$FW_URL_FILE" ]; then tr -d '\r\n' < "$FW_URL_FILE"; else echo "$FW_DEFAULT_URL"; fi
}
_job_start() {   # $1=单元名 $2=日志文件 $3...=要执行的命令
    local unit="$1" log="$2"; shift 2
    : > "$log"
    {
        echo "===== $(date '+%F %T') 开始：$unit ====="
        echo "命令：$*"
        echo
    } >> "$log" 2>&1
    systemctl reset-failed "$unit" 2>/dev/null || true
    systemd-run --unit="$unit" --collect --quiet \
        --property=StandardOutput="append:$log" \
        --property=StandardError="append:$log" \
        --property=WorkingDirectory=/ \
        "$@" >/dev/null 2>&1
    if [ $? -ne 0 ]; then echo "后台任务启动失败（systemd-run 不可用？）"; return 1; fi
    echo "已在后台开始（日志 $log）"
}
_job_status() {  # $1=单元名 $2=日志文件
    local unit="$1" log="$2" st
    st=$(systemctl is-active "$unit" 2>/dev/null || true)
    case "$st" in
        active|activating) echo "STATE=running";;
  remote-debug)
      /usr/local/bin/arch-remote-debug-ctl ${1:-status}
      ;;
  hw-info)
      /usr/local/bin/hw-detect.sh hw-info
      ;;
  hw-force-max)
      /usr/local/bin/hw-detect.sh hw-force-max ${1:-0}
      ;;
        *)                 echo "STATE=idle";;
    esac
    if [ -s "$log" ]; then
        echo "--- 日志尾部（$log）---"
        tail -n 25 "$log"
    else
        echo "STATE=idle"
        echo "（还没有日志）"
    fi
}
_apt_count() {
    apt-get -s upgrade 2>/dev/null | awk '/^[0-9]+ upgraded/{print $1; exit}' || echo 0
}

_hotspot_heal() {
    exec 9>/run/arch-hotspot-heal.lock
    flock -w 120 9 2>/dev/null || true
    # TETRA is a mutually exclusive mode. Never pull MMDVM back up if that mode
    # is the selected/active one.
    if systemctl is-active --quiet tetra-bs.service 2>/dev/null; then
        echo "当前为 TDR Link 模式，跳过常规热点自愈"
        return 0
    fi
    if ! /usr/local/bin/arch-sx-probe; then
        echo "未检测到可用的 SX1255，热点服务暂不启动"
        return 1
    fi
    touch /etc/mmdvm/soapy-sx-verified
    systemctl enable mmdvm-multi.service >/dev/null 2>&1 || true
    if [ -x /usr/local/libexec/channel-setup.py ] && [ -s /etc/mmdvm/channels.json ]; then
        if ! /usr/local/libexec/channel-setup.py apply "$(cat /etc/mmdvm/channels.json)"; then
            echo "按 channels.json 恢复通道失败"
            return 1
        fi
    else
        systemctl restart --no-block mmdvm-multi.service
    fi
    ID="$(awk -F= '/^[[:space:]]*Id=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' /etc/DMRGateway.ini 2>/dev/null || true)"
    case "$ID" in
      *[1-9]*) touch /etc/mmdvm/dmr-ready; systemctl enable --now dmrgateway.service >/dev/null 2>&1 || true ;;
      *) rm -f /etc/mmdvm/dmr-ready; systemctl disable --now dmrgateway.service >/dev/null 2>&1 || true ;;
    esac
    echo "热点服务自愈完成：已按当前通道配置恢复"
}
case "$ACT" in
  update-status)
      CACHE=/var/lib/arch/update-status.cache
      mkdir -p /var/lib/arch 2>/dev/null || true
      ARCH_CURRENT=""; ARCH_LATEST=""
      if [ -f /etc/arch-hotspot-release ]; then . /etc/arch-hotspot-release; ARCH_CURRENT="${VERSION:-}"; fi
      if [ -s "$CACHE" ] && [ -z "$(find "$CACHE" -mmin +10 2>/dev/null)" ] && [ ! /etc/arch-hotspot-release -nt "$CACHE" ]; then
          cat "$CACHE"; exit 0
      fi
      ARCH_UPDATE=no
      if [ -n "${UPDATE_BASE:-}" ] && [ -n "${CHANNEL:-}" ]; then
          TMPA=$(mktemp)
          if timeout 10 curl -4 -fsSL --retry 1 --connect-timeout 4 -m 8 "$UPDATE_BASE/$CHANNEL/current.json" -o "$TMPA"; then
              ARCH_LATEST=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version",""))' "$TMPA" 2>/dev/null || true)
          elif [ -s "$CACHE" ]; then
              rm -f "$TMPA"; cat "$CACHE"; exit 0
          fi
          rm -f "$TMPA"
      fi
      if [ -n "$ARCH_LATEST" ] && [ "$ARCH_LATEST" != "$ARCH_CURRENT" ]; then ARCH_UPDATE=yes; fi
      OUT=$(printf 'ARCH_CURRENT=%s
ARCH_LATEST=%s
ARCH_UPDATE=%s
' "$ARCH_CURRENT" "$ARCH_LATEST" "$ARCH_UPDATE")
      printf '%s
' "$OUT" | tee "$CACHE" >/dev/null
      echo "$OUT"
      ;;
  perf-info)
      echo "=== LOAD ==="
      cat /proc/loadavg 2>/dev/null || true
      uptime 2>/dev/null || true
      echo "=== TOP CPU ==="
      ps -eo pid,ppid,pcpu,pmem,etime,comm,args --sort=-pcpu 2>/dev/null | head -20
      echo "=== CPU FREQ ==="
      for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do [ -r "$f" ] && printf "%s %s\n" "$f" "$(cat "$f")"; done
      echo "=== SERVICES ==="
      systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null | head -30
      ;;
  info)
      echo "ARCH_VERSION=$(awk -F= '/^VERSION=/{print $2; exit}' /etc/arch-hotspot-release 2>/dev/null)"
      echo "ARCH_CHANNEL=$(awk -F= '/^CHANNEL=/{print $2; exit}' /etc/arch-hotspot-release 2>/dev/null)"
      echo "FIRMWARE_VERSION=$(head -1 /etc/mmdvm/arch-build-version 2>/dev/null || awk -F= '/^VERSION=/{print $2; exit}' /etc/arch-hotspot-release 2>/dev/null || echo -)"
      echo "HOSTNAME=$(hostname)"
      echo "MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null)"
      echo "KERNEL=$(uname -r)"
      echo "UPTIME=$(uptime -p 2>/dev/null || uptime)"
      echo "TEMP=$(vcgencmd measure_temp 2>/dev/null | sed 's/temp=//')"
      echo "DISK=$(df -h / | awk 'NR==2{print $3"/"$2" ("$5")"}')"
      echo "MEM=$(free -h | awk '/Mem:/{print $3"/"$2}')"
      echo "TIME=$(date '+%Y-%m-%d %H:%M:%S %Z')"
      echo "TZ=$(timedatectl show -p Timezone --value 2>/dev/null)"
      ;;
  net)
      # 简洁键值输出：系统设置页直接渲染；自动列出全部 USB/4G 网卡
      echo "HOSTNAME=$(hostname)"
      for IF in eth0 wlan0; do
          T=$(nmcli -t -f DEVICE,TYPE dev status 2>/dev/null | awk -F: -v d="$IF" '$1==d{print $2; exit}')
          [ -n "$T" ] || continue
          U=$(echo "$IF" | tr 'a-z' 'A-Z')
          ST=$(nmcli -t -f DEVICE,STATE dev status 2>/dev/null | awk -F: -v d="$IF" '$1==d{print $2; exit}')
          IP=$(ip -4 -o addr show "$IF" 2>/dev/null | awk '{print $4}' | head -1)
          GW=$(ip -4 route show default dev "$IF" 2>/dev/null | awk '{print $3; exit}')
          echo "${U}_TYPE=$T"
          echo "${U}_STATE=${ST:-unknown}"
          echo "${U}_IP=${IP:--}"
          echo "${U}_GW=${GW:--}"
          if [ "$T" = "wifi" ]; then
              WS=$(nmcli -t -f IN-USE,SSID,SIGNAL dev wifi 2>/dev/null | awk -F: '$1=="*"{print $2" ("$3"%)"; exit}')
              echo "WIFI_SSID=${WS:--}"
          fi
      done
      COUNT=0
      while IFS=: read -r D T; do
          case "$D" in ''|--|lo|eth0|wlan0) continue ;; esac
          case "$T" in ethernet|gsm|wwan|wifi) ;; *) continue ;; esac
          COUNT=$((COUNT+1))
          ST=$(nmcli -t -f DEVICE,STATE dev status 2>/dev/null | awk -F: -v d="$D" '$1==d{print $2; exit}')
          IP=$(ip -4 -o addr show "$D" 2>/dev/null | awk '{print $4}' | head -1)
          GW=$(ip -4 route show default dev "$D" 2>/dev/null | awk '{print $3; exit}')
          [ -n "$IP" ] && ST=connected
          echo "USB_NET_${COUNT}_NAME=$D"
          echo "USB_NET_${COUNT}_TYPE=$T"
          echo "USB_NET_${COUNT}_STATE=${ST:-unknown}"
          echo "USB_NET_${COUNT}_IP=${IP:--}"
          echo "USB_NET_${COUNT}_GW=${GW:--}"
          if [ "$COUNT" -eq 1 ]; then
              echo "USBETH_NAME=$D"; echo "USBETH_TYPE=$T"; echo "USBETH_STATE=${ST:-unknown}"; echo "USBETH_IP=${IP:--}"; echo "USBETH_GW=${GW:--}"
          fi
          [ "$COUNT" -ge 8 ] && break
      done < <(nmcli -t -f DEVICE,TYPE dev status 2>/dev/null)
      echo "USB_NET_COUNT=$COUNT"
      echo "DNS=$(awk '/^nameserver/{print $2}' /etc/resolv.conf 2>/dev/null | grep -viE '^(fe80:|ff0|::1$)' | grep -v '%' | head -4 | tr '\n' ',' | sed 's/,$//')"
      ;;
  wifi-list|wifi-scan)
      # 输出：INUSE|SSID|信号%|加密|信道|频率MHz|频段   （按信号从强到弱，同名只留最强）
      BE="$(_wifi_backend)"; IF="$(_wifi_iface)"
      if [ "$BE" = "nm" ]; then
          RAW=$(_nm_terse -f IN-USE,SSID,SIGNAL,SECURITY,CHAN,FREQ \
                dev wifi list --rescan yes)
          printf '%s\n' "$RAW" | awk -F'|' '
              NF>=5 && $2!="" {
                  f = (NF>=6 ? $6+0 : 0); c = (NF>=5 ? $5+0 : 0)
                  b = (c>=1 && c<=14) ? "2.4G" : (c>=32 ? "5G" : (f>=2400&&f<=2500 ? "2.4G" : (f>=4900 ? "5G" : "-")))
                  if (!(($2) in best) || $3+0 > sig[$2]) { best[$2]=1; sig[$2]=$3+0;
                    line[$2]=sprintf("%s|%s|%s|%s|%s|%s|%s", $1, $2, $3, ($4==""?"开放":$4), (c?c:"-"), (f?f:"-"), b) }
              }
              END { for (k in line) print line[k] }' | sort -t'|' -k3 -nr
      elif [ "$BE" = "wpa" ]; then
          wpa_cli -i "$IF" scan >/dev/null 2>&1
          sleep 5
          wpa_cli -i "$IF" scan_results 2>/dev/null | awk -F'\t' '
              NR>1 && $5!="" {
                  f=$2+0; b=(f>=2400&&f<=2500)?"2.4G":(f>=4900?"5G":"-")
                  sec=($4 ~ /WPA3/ ? "WPA3" : ($4 ~ /WPA2/ ? "WPA2" : ($4 ~ /WPA/ ? "WPA" : ($4 ~ /WEP/ ? "WEP" : "开放"))))
                  lvl=($3+0)+100; if (lvl<0) lvl=0; if (lvl>100) lvl=100
                  if (!(($5) in best) || $3+0 > sig[$5]) { best[$5]=1; sig[$5]=$3+0;
                    line[$5]=sprintf(" |%s|%s|%s|%s|%s|%s", $5, lvl, sec, "-", f, b) }
              }
              END { for (k in line) print line[k] }' | sort -t'|' -k3 -nr
      else
          echo "未检测到 WiFi 后端（NetworkManager 未运行且无 wpa_supplicant）"
      fi
      ;;
  wifi-status)
      BE="$(_wifi_backend)"; IF="$(_wifi_iface)"
      echo "BACKEND=$BE"
      echo "IFACE=${IF:--}"
      echo "RADIO=$(_wifi_radio_state)"
      if [ "$BE" = "nm" ]; then
          CON=$(nmcli -t -f NAME,TYPE connection show --active 2>/dev/null | awk -F: '$2=="802-11-wireless"{print $1; exit}')
          echo "CONN=${CON:--}"
          if [ -n "${CON:-}" ]; then
              echo "SSID=$(nmcli -g 802-11-wireless.ssid connection show "$CON" 2>/dev/null)"
              echo "AUTOCONNECT=$(nmcli -g connection.autoconnect connection show "$CON" 2>/dev/null)"
              echo "PRIORITY=$(nmcli -g connection.autoconnect-priority connection show "$CON" 2>/dev/null)"
              echo "KEYMGMT=$(nmcli -g 802-11-wireless-security.key-mgmt connection show "$CON" 2>/dev/null)"
          fi
          _nm_terse -f IN-USE,SSID,SIGNAL,FREQ,RATE,SECURITY dev wifi list \
            | awk -F'|' '$1=="*"{print "SIGNAL="$3"%"; print "FREQ="$4; print "RATE="$5; print "WIFISEC="$6; exit}'
      else
          ST=$(wpa_cli -i "$IF" status 2>/dev/null)
          printf '%s\n' "$ST" | awk -F= '/^ssid=/{print "SSID="$2} /^wpa_state=/{print "WPASTATE="$2} /^ip_address=/{print "IP="$2}'
      fi
      FR=$(_nm_terse -f IN-USE,FREQ dev wifi list | awk -F'|' '$1=="*"{print $2; exit}')
      if [ -n "${FR:-}" ]; then
          echo "FREQ=$FR"; echo "BAND=$(_wifi_band "$(printf '%s' "$FR" | tr -dc 0-9)")"
      fi
      if [ -z "${IP:-}" ]; then
          IPLINE=$(ip -4 -o addr show "$IF" 2>/dev/null | awk '{print $4}' | head -1)
          echo "IP=${IPLINE:--}"
      fi
      echo "GW=$(ip -4 route 2>/dev/null | awk '/^default/{print $3; exit}')"
      echo "SAVED=$(_wifi_saved_count)"
      echo "BANDS=$(iwlist "$IF" freq 2>/dev/null | grep -c GHz)"
      ;;

  wifi-saved)
      # 输出：连接名|SSID|自动连接|优先级|加密
      BE="$(_wifi_backend)"; IF="$(_wifi_iface)"
      if [ "$BE" = "nm" ]; then
          _nm_terse -f NAME,TYPE connection show \
            | awk -F'|' '$2=="802-11-wireless"{print $1}' | while IFS= read -r CN; do
                  [ -n "$CN" ] || continue
                  SS=$(nmcli -g 802-11-wireless.ssid connection show "$CN" 2>/dev/null)
                  AC=$(nmcli -g connection.autoconnect connection show "$CN" 2>/dev/null)
                  PR=$(nmcli -g connection.autoconnect-priority connection show "$CN" 2>/dev/null)
                  KM=$(nmcli -g 802-11-wireless-security.key-mgmt connection show "$CN" 2>/dev/null)
                  printf '%s|%s|%s|%s|%s\n' "$CN" "${SS:--}" "${AC:-yes}" "${PR:-0}" "${KM:-open}"
              done
      else
          awk '
            /network=\{/ { inb=1; ssid=""; km="open"; hid=""; next }
            inb && /\}/  { if (ssid=="") ssid="-"; printf "%s|%s|yes|0|%s%s\n", ssid, ssid, km, hid; inb=0; next }
            inb && /^[ \t]*ssid=/ { s=$0; sub(/^[ \t]*ssid=/,"",s); gsub(/"/,"",s); gsub(/\r/,"",s); ssid=s }
            inb && /^[ \t]*psk=/ { km="WPA/PSK" }
            inb && /^[ \t]*key_mgmt=NONE/ { km="open" }
            inb && /^[ \t]*scan_ssid=1/ { hid="(隐藏)" }
          ' /etc/wpa_supplicant/wpa_supplicant.conf 2>/dev/null
      fi
      ;;
  wifi-handoff)
      SSID="${1:-}"; PW="${2:-}"; HID="${3:-0}"
      [ -n "$SSID" ] || { echo "?? SSID"; exit 2; }
      echo "??? WiFi ???5 ????? $SSID"
      sleep 5
      exec "$0" wifi-connect "$SSID" "$PW" "$HID"
      ;;
  wifi-connect)
      SSID="${1:-}"; PW="${2:-}"; HID="${3:-0}"
      [ -n "$SSID" ] || { echo "缺少 SSID"; exit 2; }
      BE="$(_wifi_backend)"; IF="$(_wifi_iface)"
      if [ "$BE" = "nm" ]; then
          _wifi_radio_on
          if [ -n "$PW" ]; then
              if [ "$HID" = "1" ]; then OUT=$(nmcli --wait 45 dev wifi connect "$SSID" password "$PW" hidden yes 2>&1)
              else OUT=$(nmcli --wait 45 dev wifi connect "$SSID" password "$PW" 2>&1); fi
          else
              if [ "$HID" = "1" ]; then OUT=$(nmcli --wait 45 dev wifi connect "$SSID" hidden yes 2>&1)
              else OUT=$(nmcli --wait 45 dev wifi connect "$SSID" 2>&1); fi
          fi
          RC=$?
          echo "$OUT"
          if [ $RC -eq 0 ]; then
              CN=$(_wifi_conn_of_ssid "$SSID")
              [ -n "$CN" ] && _wifi_fix_profile "$CN"
              _wpa_backup_add "$SSID" "$PW" "$HID"
              echo "已保存并设为开机自动连接（连接名: ${CN:-$SSID}）"
              echo "---- 当前 WiFi 状态 ----"
              _nm_terse -f IN-USE,SSID,SIGNAL,FREQ,RATE dev wifi list \
                | awk -F'|' '$1=="*"{print "  SSID="$2"  信号="$3"%  频率="$4"  速率="$5; exit}'
          fi
          exit $RC
      elif [ "$BE" = "wpa" ]; then
          _wifi_radio_on
          ID=$(wpa_cli -i "$IF" add_network 2>/dev/null | tail -1)
          case "$ID" in ''|*[!0-9]*) echo "wpa_cli add_network 失败（后端未就绪）"; exit 3 ;; esac
          wpa_cli -i "$IF" set_network "$ID" ssid "\"$SSID\"" >/dev/null 2>&1
          if [ -n "$PW" ]; then wpa_cli -i "$IF" set_network "$ID" psk "\"$PW\"" >/dev/null 2>&1
          else wpa_cli -i "$IF" set_network "$ID" key_mgmt NONE >/dev/null 2>&1; fi
          [ "$HID" = "1" ] && wpa_cli -i "$IF" set_network "$ID" scan_ssid 1 >/dev/null 2>&1
          wpa_cli -i "$IF" enable_network "$ID" >/dev/null 2>&1
          wpa_cli -i "$IF" select_network "$ID" >/dev/null 2>&1
          wpa_cli -i "$IF" save_config >/dev/null 2>&1 || echo "注意：save_config 失败，重启后可能不会自动重连"
          _wpa_backup_add "$SSID" "$PW" "$HID"
          sleep 8
          wpa_cli -i "$IF" status 2>/dev/null | grep -E '^(ssid|wpa_state|ip_address|key_mgmt)='
      else
          echo "未检测到可用的 WiFi 后端（NetworkManager / wpa_supplicant 都没有）"; exit 3
      fi
      ;;
  wifi-hidden)
      exec "$0" wifi-connect "$1" "${2:-}" 1
      ;;

  wifi-disconnect)
      BE="$(_wifi_backend)"; IF="$(_wifi_iface)"
      if [ "$BE" = "nm" ]; then
          if [ -n "$IF" ]; then nmcli dev disconnect "$IF" 2>&1; else echo "没有活动的 WiFi 接口"; fi
      else
          wpa_cli -i "$IF" disconnect 2>&1 || echo "断开失败"
      fi
      ;;
  wifi-forget)
      T="${1:-}"
      [ -n "$T" ] || { echo "缺少要删除的连接名或 SSID"; exit 2; }
      if [ "$(_wifi_backend)" = "nm" ]; then
          CN=""
          nmcli -t -f NAME connection show 2>/dev/null | grep -qxF "$T" && CN="$T"
          [ -z "$CN" ] && CN=$(_wifi_conn_of_ssid "$T")
          [ -z "$CN" ] && CN="$T"
          nmcli connection delete "$CN" 2>&1
      else
          python3 - "$T" <<'WPADEL'
import re, sys
ssid = sys.argv[1]
p = "/etc/wpa_supplicant/wpa_supplicant.conf"
try:
    s = open(p, encoding="utf-8", errors="ignore").read()
except Exception as e:
    print("读取 %s 失败: %s" % (p, e)); sys.exit(1)
blocks = re.findall(r"network=\{[^}]*\}", s, re.S)
keep, removed = [], 0
for b in blocks:
    m = re.search(r'ssid="([^"]*)"', b)
    if m and m.group(1) == ssid:
        removed += 1; continue
    keep.append(b)
rest = re.sub(r"network=\{[^}]*\}", "", s, flags=re.S).strip()
open(p + ".bak", "w", encoding="utf-8").write(s)
open(p, "w", encoding="utf-8").write(rest + "\n" + "\n".join(keep) + "\n")
print("已删除 %d 个 SSID=%s 的配置（原文件已备份为 wpa_supplicant.conf.bak）" % (removed, ssid))
WPADEL
      fi
      ;;
  wifi-reconnect)
      T="${1:-}"; [ -n "$T" ] || { echo "缺少连接名"; exit 2; }
      if [ "$(_wifi_backend)" = "nm" ]; then nmcli connection up "$T" 2>&1
      else wpa_cli -i "$(_wifi_iface)" reconfigure 2>&1; fi
      ;;
  wifi-radio)
      R="${1:-on}"
      if [ "$R" = "off" ]; then
          command -v nmcli >/dev/null 2>&1 && nmcli radio wifi off 2>&1
          rfkill block wifi 2>&1
          echo "WiFi 射频已关闭（省电/防干扰；网线仍可用）"
      else
          rfkill unblock wifi 2>&1
          command -v nmcli >/dev/null 2>&1 && nmcli radio wifi on 2>&1
          echo "WiFi 射频已打开"
      fi
      ;;
  wifi-country)
      C="CN"      # ★ 国家/地区码固定为 CN（不再允许界面修改）
      if command -v raspi-config >/dev/null 2>&1; then
          raspi-config nonint do_wifi_country "$C" >/dev/null 2>&1 && echo "已设置地区：$C"
      fi
      CONF=$(_wpa_conf)
      if [ -f "$CONF" ]; then
          if grep -q '^country=' "$CONF"; then sed -i "s/^country=.*/country=$C/" "$CONF"
          else sed -i "1i country=$C" "$CONF"; fi
          echo "已写入 $CONF : country=$C"
      else
          mkdir -p "$(dirname "$CONF")"
          printf 'ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1\ncountry=%s\n' "$C" > "$CONF"
          echo "已创建 $CONF 并写入 country=$C"
      fi
      mkdir -p /etc/default
      if [ -f /etc/default/crda ]; then sed -i "s/^REGDOMAIN=.*/REGDOMAIN=$C/" /etc/default/crda
      else echo "REGDOMAIN=$C" > /etc/default/crda; fi
      iw reg set "$C" 2>/dev/null || true
      sleep 1
      echo "当前管制域：$(iw reg get 2>/dev/null | awk '/^country/{print $2; exit}' | tr -d ':')（固定 CN）"
      ;;
  wifi-priority)
      T="${1:-}"; P="${2:-0}"
      [ -n "$T" ] || { echo "缺少连接名"; exit 2; }
      case "$P" in ''|*[!0-9-]*) echo "优先级必须是整数"; exit 2 ;; esac
      nmcli connection modify "$T" connection.autoconnect-priority "$P" 2>&1 && echo "已设置 $T 自动连接优先级 = $P"
      ;;
  wifi-autoconnect)
      T="${1:-}"; V="${2:-yes}"
      [ -n "$T" ] || { echo "缺少连接名"; exit 2; }
      case "$V" in yes|no) : ;; *) echo "取值必须是 yes / no"; exit 2 ;; esac
      nmcli connection modify "$T" connection.autoconnect "$V" 2>&1 && echo "$T 自动连接 = $V"
      ;;
  wifi-info)
      T="${1:-}"
      [ -n "$T" ] || { echo "缺少连接名"; exit 2; }
      echo "--- $T ---"
      for k in 802-11-wireless.ssid connection.autoconnect connection.autoconnect-priority \
               802-11-wireless-security.key-mgmt 802-11-wireless.band ipv4.method; do
          printf '  %-40s %s\n' "$k" "$(nmcli -g "$k" connection show "$T" 2>/dev/null)"
      done
      ;;

  hotspot-heal)
      _hotspot_heal
      ;;
  restart-hotspot)
      if ! _hotspot_heal; then
          systemctl enable --now mmdvm-multi.service 2>/dev/null || true
          systemctl restart --no-block mmdvm-multi.service 2>/dev/null || true
      fi
      sleep 3
      for g in ircddbgatewayd ysfgateway p25gateway nxdngateway; do
          systemctl restart --no-block "$g" 2>/dev/null || true
      done
      echo "已重启热点服务"
      ;;
  restart-web)  systemctl restart --no-block mmdvm-web; echo "已重启 Web 控制台" ;;
  reboot)       systemctl reboot ;;
  shutdown)     systemctl poweroff ;;
  timezone)     timedatectl set-timezone "${1:-Asia/Shanghai}" 2>&1 && echo "时区已设为 ${1:-Asia/Shanghai}" ;;
  ntp)          timedatectl set-ntp "${1:-true}" 2>&1 && echo "NTP=${1:-true}" ;;
  time-status)
      timedatectl status 2>/dev/null | sed 's/^ *//'
      echo "NTP=$(timedatectl show -p NTP --value 2>/dev/null)"
      echo "SYNCED=$(timedatectl show -p NTPSynchronized --value 2>/dev/null)"
      echo "TZ=$(timedatectl show -p Timezone --value 2>/dev/null)"
      echo "NOW=$(date '+%Y-%m-%d %H:%M:%S %Z')"
      if systemctl is-enabled mmdvm-sched-reboot.timer >/dev/null 2>&1; then
          echo "SCHED=on"
          echo "SCHED_CAL=$(systemctl show -p TimersCalendar --value mmdvm-sched-reboot.timer 2>/dev/null)"
          echo "SCHED_NEXT=$(systemctl show -p NextElapseUSecRealtime --value mmdvm-sched-reboot.timer 2>/dev/null)"
      else
          echo "SCHED=off"
      fi
      ;;
  ntp)
      case "${1:-on}" in on|true|1) timedatectl set-ntp true ;; *) timedatectl set-ntp false ;; esac
      sleep 1
      echo "NTP=$(timedatectl show -p NTP --value 2>/dev/null)"
      ;;
  timesync)
      systemctl restart systemd-timesyncd 2>/dev/null || true
      sleep 2
      echo "SYNCED=$(timedatectl show -p NTPSynchronized --value 2>/dev/null)"
      date '+NOW=%Y-%m-%d %H:%M:%S %Z'
      ;;
  tz)
      Z="${1:-}"
      [ -n "$Z" ] || { echo "缺少时区（例如 Asia/Shanghai）"; exit 2; }
      timedatectl set-timezone "$Z" && echo "时区已设为 $Z" && date '+NOW=%Y-%m-%d %H:%M:%S %Z'
      ;;
  sched-reboot)
      ON="${1:-off}"; HHMM="${2:-04:00}"; MODE="${3:-daily}"
      if [ "$ON" = "on" ]; then
          case "$MODE" in
            weekly) CAL="Mon *-*-* $HHMM:00" ;;
  hw-info)
      /usr/local/bin/hw-detect.sh hw-info
      ;;
  hw-force-max)
      /usr/local/bin/hw-detect.sh hw-force-max ${1:-0}
      ;;
            *)      CAL="*-*-* $HHMM:00" ;;
          esac
          printf '%s\n' '[Unit]' 'Description=Scheduled reboot for MMDVM hotspot' '[Service]' \
              'Type=oneshot' 'ExecStart=/sbin/shutdown -r +1 "scheduled reboot (web console)"' \
              > /etc/systemd/system/mmdvm-sched-reboot.service
          printf '%s\n' '[Unit]' 'Description=Scheduled reboot timer' '[Timer]' \
              "OnCalendar=$CAL" 'Persistent=true' '[Install]' 'WantedBy=timers.target' \
              > /etc/systemd/system/mmdvm-sched-reboot.timer
          systemctl daemon-reload
          systemctl enable --now mmdvm-sched-reboot.timer >/dev/null 2>&1
          echo "定时重启已开启：$CAL"
      else
          systemctl disable --now mmdvm-sched-reboot.timer >/dev/null 2>&1
          rm -f /etc/systemd/system/mmdvm-sched-reboot.timer /etc/systemd/system/mmdvm-sched-reboot.service
          systemctl daemon-reload 2>/dev/null || true
          echo "定时重启已关闭"
      fi
      ;;
  factory-reset)
      if [ "${1:-}" = "net" ]; then
          /usr/local/bin/arch-factory-reset.sh --reset-network
      else
          /usr/local/bin/arch-factory-reset.sh
      fi
      ;;
  backup)
      if [ -x /usr/local/bin/backup.sh ]; then /usr/local/bin/backup.sh 2>&1 | tail -6
      else echo "backup.sh 不存在"; fi
      ;;
  logs)
      SVC="${1:-mmdvm-multi}"; N="${2:-60}"
      journalctl -u "$SVC" -n "$N" --no-pager 2>&1 | tail -n "$N"
      ;;
  ping)
      H="${1:-223.5.5.5}"
      ping -c 3 -W 2 "$H" 2>&1 | tail -4
      ;;
  sys-upgrade-count)
      echo "UPGRADABLE=$(_apt_count)"
      apt-get -s upgrade 2>/dev/null | grep '^Inst' | head -20 | sed 's/^/  /'
      ;;
  sys-update-check)
      echo "正在刷新软件源索引（apt-get update -qq，约 10-60 秒）…"
      timeout 150 apt-get update -qq 2>&1 | tail -6
      echo "UPGRADABLE=$(_apt_count)"
      echo "CHECKED_AT=$(date '+%F %T')"
      ;;
  sys-update)
      _job_start mmdvm-sysupdate "$JOB_LOG_DIR/mmdvm-sysupdate.log" \
        /bin/bash -c 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::=--force-confold upgrade && echo "--- 自动清理 ---" && apt-get -y autoremove && echo "===== 系统更新完成 ====="'
      ;;
  sys-update-status)
      _job_status mmdvm-sysupdate "$JOB_LOG_DIR/mmdvm-sysupdate.log"
      ;;
  fw-info)
      echo "ARCH_VERSION=$(awk -F= '/^VERSION=/{print $2; exit}' /etc/arch-hotspot-release 2>/dev/null)"
      echo "ARCH_CHANNEL=$(awk -F= '/^CHANNEL=/{print $2; exit}' /etc/arch-hotspot-release 2>/dev/null)"
      echo "CURRENT_VERSION=$(head -1 /etc/mmdvm/arch-build-version 2>/dev/null || awk -F= '/^VERSION=/{print $2; exit}' /etc/arch-hotspot-release 2>/dev/null || echo -)"
      echo "ENGINE_VERSION=$(head -1 /etc/mmdvm/firmware-version 2>/dev/null || awk -F= '/^VERSION=/{print $2; exit}' /etc/mmdvm/install-state.conf 2>/dev/null || echo -)"
      echo "MANIFEST_URL=$(_fw_url)"
      echo "URL=$(_fw_url)"
      B=/usr/local/bin/MMDVM-Multi
      if [ -f "$B" ]; then
          echo "ENGINE_DATE=$(date -r "$B" '+%F %T')"
          echo "ENGINE_SIZE=$(stat -c %s "$B")"
          echo "ENGINE_MD5=$(md5sum "$B" | awk '{print $1}')"
      else
          echo "ENGINE_DATE=-"
      fi
      echo "ENGINE_SVC=$(systemctl is-active mmdvm-multi 2>/dev/null)"
      echo "ENGINE_SINCE=$(systemctl show -p ActiveEnterTimestamp --value mmdvm-multi 2>/dev/null)"
      ST=/etc/mmdvm/install-state.conf
      AM=/etc/mmdvm/active-mode.conf
      echo "CFG_VERSION=$(awk -F= '/^VERSION=/{print $2; exit}' "$ST" 2>/dev/null)"
      echo "CFG_CHANNELS=$(awk -F= '/^CHANNELS=/{print $2; exit}' "$AM" 2>/dev/null)"
      echo "CFG_MODE=$(awk -F= '/^MODE=/{print $2; exit}' "$AM" 2>/dev/null)"
      echo "CFG_CH_JSON=$(python3 -c "import json;d=json.load(open('/etc/mmdvm/channels.json'));print(d.get('channels') if isinstance(d,dict) else len(d))" 2>/dev/null || echo -)"
      echo "CFG_C_SOAPY=$(awk -F= '/^SOAPYSX_COMMIT=/{print substr($2,1,8); exit}' "$ST" 2>/dev/null)"
      echo "CFG_C_HOST=$(awk -F= '/^MMDVM_HOST_COMMIT=/{print substr($2,1,8); exit}' "$ST" 2>/dev/null)"
      echo "CFG_C_IQ=$(awk -F= '/^MMDVM_IQ_COMMIT=/{print substr($2,1,8); exit}' "$ST" 2>/dev/null)"
      echo "CFG_C_DMR=$(awk -F= '/^DMRGATEWAY_COMMIT=/{print substr($2,1,8); exit}' "$ST" 2>/dev/null)"
      echo "SRC_DIR=$(ls -d /opt/src-mmdvm/MMDVM-Multi 2>/dev/null || echo -)"
      ;;
  fw-url)
      if [ -n "${1:-}" ]; then
          mkdir -p "$(dirname "$FW_URL_FILE")"
          printf '%s\n' "$1" > "$FW_URL_FILE"
          echo "发布页地址已保存：$1"
      fi
      echo "URL=$(_fw_url)"
      ;;
  fw-check)
      /usr/local/bin/arch-fw-online.sh check "$(_fw_url)"
      ;;
  fw-online-update)
      U="$(_fw_url)"
      _job_start mmdvm-fwonline "$JOB_LOG_DIR/mmdvm-firmware.log" \
        /usr/local/bin/arch-fw-online.sh update "$U"
      ;;
  hotspot-update)
      _job_start arch-hotspot-update "$JOB_LOG_DIR/arch-hotspot-update.log" \
        /usr/local/bin/arch-hotspot-update
      ;;
  hotspot-update-status)
      _job_status arch-hotspot-update "$JOB_LOG_DIR/arch-hotspot-update.log"
      ;;
  sys-update-clean)
      _job_start mmdvm-aptclean "$JOB_LOG_DIR/mmdvm-sysupdate.log" \
        /bin/bash -c 'apt-get -y autoremove; apt-get -y autoclean; apt-get -y clean; echo "===== 清理完成 ====="'
      ;;
  boot-fw-status)
      echo "--- 引导固件 (rpi-eeprom-update) ---"
      rpi-eeprom-update 2>&1 | head -12
      echo "--- 系统 ---"
      echo "MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null)"
      echo "KERNEL=$(uname -r)"
      echo "OS=$(awk -F= '/^PRETTY_NAME/{gsub(/"/,"",$2); print $2}' /etc/os-release 2>/dev/null)"
      echo "BOOT_CONFIG_REBOOT=$( [ -f /var/run/reboot-required ] && echo yes || echo no )"
      ;;
  boot-fw-update)
      _job_start mmdvm-bootfw "$JOB_LOG_DIR/mmdvm-firmware.log" \
        /bin/bash -c 'echo "== 当前状态 =="; rpi-eeprom-update; echo; echo "== 应用更新 (rpi-eeprom-update -a) =="; rpi-eeprom-update -a; echo; echo "新引导固件将在下次重启后生效"; echo "===== 完成 ====="'
      ;;
  fw-download)
      U="${1:-}"
      [ -n "$U" ] || { echo "缺少下载地址"; exit 2; }
      _job_start mmdvm-fwdl "$JOB_LOG_DIR/mmdvm-firmware.log" \
        /bin/bash -c 'cd /tmp; rm -f mmdvm-fw.pkg; echo "下载地址: $1"; curl -L --retry 2 --connect-timeout 20 -m 900 -A "Mozilla/5.0" -o /tmp/mmdvm-fw.pkg -w "HTTP=%{http_code}  大小=%{size_download} 字节\n" "$1"; echo; file /tmp/mmdvm-fw.pkg; echo "===== 下载完成，可点「安装已下载的包」 ====="' _ "$U"
      ;;
  fw-install)
      F="${1:-/tmp/mmdvm-fw.pkg}"
      [ -f "$F" ] || { echo "找不到安装包：$F（先下载或上传）"; exit 2; }
      _job_start mmdvm-fwinst "$JOB_LOG_DIR/mmdvm-firmware.log" \
        /bin/bash -c '
F="$1"; D=/tmp/mmdvm-fw-extract
echo "安装包：$F"; file "$F"; ls -l "$F"
cp -a /usr/local/bin/MMDVM-Multi "/tmp/MMDVM-Multi.before.$(date +%Y%m%d-%H%M%S)" 2>/dev/null && echo "已备份当前引擎到 /tmp"
rm -rf "$D"; mkdir -p "$D"
case "$F" in
  *.deb)
      echo "== 使用 dpkg 安装 =="; dpkg -i "$F" 2>&1 | tail -25 ;;
  *.zip)
      echo "== 解压 zip =="; unzip -o "$F" -d "$D" >/dev/null 2>&1 || { echo "解压失败（unzip 报错）"; exit 1; } ;;
  hw-info)
      /usr/local/bin/hw-detect.sh hw-info
      ;;
  hw-force-max)
      /usr/local/bin/hw-detect.sh hw-force-max ${1:-0}
      ;;
  *)
      echo "== 解压 tar =="
      tar -xzf "$F" -C "$D" 2>/dev/null || tar -xf "$F" -C "$D" 2>/dev/null || { echo "无法识别的压缩包（支持 .tar.gz/.tgz/.tar/.zip/.deb）"; exit 1; } ;;
esac
echo "--- 包内文件（前 25 个）---"
find "$D" -type f 2>/dev/null | head -25
BIN=$(find "$D" -type f -name MMDVM-Multi | head -1)
SH=$(find "$D" -type f -name "*.sh" | head -1)
if [ -n "$BIN" ]; then
    echo "== 安装新引擎 =="
    install -m 755 "$BIN" /usr/local/bin/MMDVM-Multi && echo "已替换 /usr/local/bin/MMDVM-Multi"
    systemctl restart --no-block mmdvm-multi; sleep 5
    echo "mmdvm-multi 状态：$(systemctl is-active mmdvm-multi)"
    echo "新引擎 MD5：$(md5sum /usr/local/bin/MMDVM-Multi | cut -d" " -f1)"
elif [ -n "$SH" ]; then
    echo "== 执行包内脚本：$SH =="
    chmod +x "$SH"
    ( cd "$(dirname "$SH")" && bash "$(basename "$SH")" ) 2>&1 | tail -45
else
    echo "包内没有 MMDVM-Multi 可执行文件，也没有安装脚本；已解压到 $D ，请手动处理"
fi
echo "===== 固件更新流程结束 ====="
' _ "$F"
      ;;
  fw-log)
      L="$JOB_LOG_DIR/mmdvm-firmware.log"
      if [ -s "$L" ]; then tail -n "${1:-60}" "$L"; else echo "还没有固件更新日志"; fi
      ;;


  wifi-restore)
      BE="$(_wifi_backend)"
      if [ "$BE" != "nm" ]; then
          echo "当前后端是 $BE（wpa_supplicant 的配置本身就是持久化的），无需恢复"; exit 0
      fi
      _wifi_radio_on
      N=0; R=0
      while IFS="$(printf '\t')" read -r S P; do
          [ -n "${S:-}" ] || continue
          N=$((N+1))
          CN=$(_wifi_conn_of_ssid "$S")
          if [ -n "$CN" ]; then
              _wifi_fix_profile "$CN"
              echo "  已存在配置：$S（已设为自动连接）"
              continue
          fi
          if [ -n "${P:-}" ]; then nmcli dev wifi connect "$S" password "$P" >/dev/null 2>&1
          else nmcli dev wifi connect "$S" >/dev/null 2>&1; fi
          if [ $? -eq 0 ]; then
              _wifi_fix_profile "$(_wifi_conn_of_ssid "$S")"
              R=$((R+1)); echo "  已从备份重建：$S ✓"
          else
              echo "  重建失败（热点可能不在范围内）：$S"
          fi
      done <<EOF
$(_wpa_backup_list)
EOF
      echo "备份中的网络 $N 个，本次重建 $R 个"
      ;;
  wifi-boot)
      nmcli radio wifi on >/dev/null 2>&1 || true
      rfkill unblock wifi 2>/dev/null || true
      if [ -z "$(nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="802-11-wireless"')" ]; then
          "$0" wifi-restore >/dev/null 2>&1 || true
      fi
      CONN=$(nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="802-11-wireless"{print $1; exit}')
      if [ -n "${CONN:-}" ]; then
          nmcli connection modify "$CONN" connection.autoconnect yes >/dev/null 2>&1 || true
          nmcli connection up "$CONN" >/dev/null 2>&1 || true
      fi
      IF="$(_wifi_iface)"
      echo "BACKEND=$(_wifi_backend)"
      echo "RADIO=$(_wifi_radio_state)"
      echo "PROFILES=$(_wifi_saved_count)"
      echo "SSID=$(nmcli -t -f IN-USE,SSID dev wifi 2>/dev/null | awk -F: '$1=="*"{print $2; exit}')"
      echo "IP=$(ip -4 -o addr show "$IF" 2>/dev/null | awk '{print $4}' | head -1)"
      ;;
  net-info)
      IF="${1:-eth0}"
      CN=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | awk -F: -v d="$IF" '$2==d{print $1; exit}')
      [ -n "${CN:-}" ] || CN=$(nmcli -t -f NAME,DEVICE connection show 2>/dev/null | awk -F: -v d="$IF" '$2==d{print $1; exit}')
      A=$(nmcli -g ipv4.addresses connection show "$CN" 2>/dev/null | head -1)
      P="${A#*/}"; [ "$P" = "$A" ] && P=""
      echo "IFACE=$IF"
      echo "CONN=${CN:-}"
      echo "TYPE=$(nmcli -g connection.type connection show "$CN" 2>/dev/null)"
      echo "METHOD=$(nmcli -g ipv4.method connection show "$CN" 2>/dev/null)"
      echo "IP=${A%%/*}"
      echo "PREFIX=$P"
      [ -n "$P" ] && echo "MASK=$(_prefix2mask "$P")"
      echo "GW=$(nmcli -g ipv4.gateway connection show "$CN" 2>/dev/null)"
      echo "DNS=$(nmcli -g ipv4.dns connection show "$CN" 2>/dev/null | tr ' ' ',')"
      echo "AUTOCONNECT=$(nmcli -g connection.autoconnect connection show "$CN" 2>/dev/null)"
      echo "CURIP=$(ip -4 -o addr show "$IF" 2>/dev/null | awk '{print $4}' | head -1)"
      echo "CURGW=$(ip -4 route show default dev "$IF" 2>/dev/null | awk '{print $3; exit}')"
      echo "IFACES=$(nmcli -t -f DEVICE,TYPE dev status 2>/dev/null | awk -F: '$2=="ethernet"||$2=="wifi"{printf "%s(%s) ", $1, $2}')"
      ;;
  net-set)
      IF="${1:-}"; IP="${2:-}"; MASK="${3:-}"; GW="${4:-}"; DNS="${5:-}"; MODE="${6:-static}"
      [ -n "$IF" ] || { echo "缺少网卡名"; exit 2; }
      CN=$(nmcli -t -f NAME,DEVICE connection show 2>/dev/null | awk -F: -v d="$IF" '$2==d{print $1; exit}')
      [ -n "${CN:-}" ] || { echo "找不到网卡 $IF 的连接配置（先用界面连接该网络）"; exit 3; }
      if [ "$MODE" = "dhcp" ]; then
          nmcli connection modify "$CN" ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns "" 2>&1
          OUT=$(nmcli connection up "$CN" 2>&1 | tail -3)
          echo "已改为自动获取（DHCP）：$IF"
          echo "$OUT"
      else
          [ -n "$IP" ] || { echo "缺少 IP 地址"; exit 2; }
          P=$(_mask2prefix "$MASK")
          case "$P" in ''|*[!0-9]*) echo "子网掩码非法（示例 255.255.255.0 或 24）"; exit 2 ;; esac
          case "$IP" in *[!0-9.]*) echo "IP 非法（示例 192.168.7.60）"; exit 2 ;; esac
          nmcli connection modify "$CN" ipv4.method manual ipv4.addresses "$IP/$P" 2>&1
          if [ -n "$GW" ]; then
              nmcli connection modify "$CN" ipv4.gateway "$GW" 2>&1
          else
              nmcli connection modify "$CN" ipv4.gateway "" 2>&1
          fi
          if [ -n "$DNS" ]; then
              nmcli connection modify "$CN" ipv4.dns "$(printf '%s' "$DNS" | tr ',' ' ')" ipv4.ignore-auto-dns yes 2>&1
          else
              nmcli connection modify "$CN" ipv4.dns "" ipv4.ignore-auto-dns no 2>&1
          fi
          nmcli connection modify "$CN" connection.autoconnect yes 2>&1
          OUT=$(nmcli connection up "$CN" 2>&1 | tail -3)
          echo "已应用静态地址：$IF = $IP/$P  网关 $GW  DNS ${DNS:-（无）}"
          echo "$OUT"
          echo "注意：若 IP 与访问本页面的电脑不在同一网段，页面会立刻断开，请用新地址重新访问"
      fi
      echo "--- 当前状态 ---"
      echo "CURIP=$(ip -4 -o addr show "$IF" 2>/dev/null | awk '{print $4}' | head -1)"
      echo "CURGW=$(ip -4 route show default dev "$IF" 2>/dev/null | awk '{print $3; exit}')"
      ;;
  fix-units)
      # \u2605 \u7edf\u4e00\u4fee\u6b63\u7f51\u5173\u5355\u5143\uff1a\u5f00\u673a\u7b49\u7f51\u7edc\u5c31\u7eea + \u65ad\u7ebf\u65e0\u9650\u91cd\u8bd5\uff08\u907f\u514d\u300c\u91cd\u542f\u540e\u4e0d\u81ea\u52a8\u8fde\u670d\u52a1\u5668\u300d\uff09
      W=/etc/systemd/system
      mkdir -p "$W"
      for x in dmrgateway:DMRGateway:DMRGateway.ini \
               ysfgateway:YSFGateway:YSFGateway.ini \
               p25gateway:P25Gateway:P25Gateway.ini \
               nxdngateway:NXDNGateway:NXDNGateway.ini; do
          U="${x%%:*}"; rest="${x#*:}"; B="${rest%%:*}"; I="${rest#*:}"
          cat > "$W/$U.service" <<UNIT
[Unit]
Description=$B Service
After=network-online.target network.target mmdvm-multi.service
Wants=network-online.target mmdvm-multi.service
[Service]
Type=simple
ExecStart=/usr/local/bin/$B /etc/$I
Restart=always
RestartSec=10
User=root
[Install]
WantedBy=multi-user.target
UNIT
          chmod 644 "$W/$U.service"
      done
      if [ -f "$W/ircddbgatewayd.service" ]; then
          sed -i 's/^Restart=.*/Restart=always/' "$W/ircddbgatewayd.service"
          grep -q '^RestartSec=' "$W/ircddbgatewayd.service" || sed -i '/^Restart=always/a RestartSec=10' "$W/ircddbgatewayd.service"
      fi
      cat > "$W/mmdvm-wifi-boot.service" <<'UNITW'
[Unit]
Description=MMDVM WiFi auto-connect at boot
After=NetworkManager.service network-online.target
Wants=NetworkManager.service
[Service]
Type=oneshot
ExecStart=/usr/local/bin/sys-manage.sh wifi-boot
[Install]
WantedBy=multi-user.target
UNITW
      chmod 644 "$W/mmdvm-wifi-boot.service"
      systemctl daemon-reload
      systemctl enable mmdvm-wifi-boot.service >/dev/null 2>&1 || true
      for g in dmrgateway ysfgateway p25gateway nxdngateway ircddbgatewayd; do
          systemctl is-enabled "$g" >/dev/null 2>&1 || systemctl enable "$g" >/dev/null 2>&1 || true
      done
      echo "已修正：网关单元（开机自动重连 + 断线重试）与 WiFi 开机自检单元"
      echo "--- 当前单元 ---"
      for g in dmrgateway ysfgateway p25gateway nxdngateway ircddbgatewayd mmdvm-wifi-boot; do
          printf '  %-18s %-9s After=%s\n' "$g" "$(systemctl is-active $g 2>/dev/null)" \
              "$(systemctl show -p After --value $g 2>/dev/null | tr ' ' ',' | cut -c1-70)"
      done
      ;;
  hw-info)
      /usr/local/bin/hw-detect.sh hw-info
      ;;
  hw-force-max)
      /usr/local/bin/hw-detect.sh hw-force-max ${1:-0}
      ;;
  *) echo "用法: sys-manage.sh {perf-info|info|net|wifi-list|wifi-connect <SSID> [密码]|wifi-disconnect|wifi-forget <连接名>|wifi-reconnect <连接名>|wifi-radio <on|off>|wifi-country <CN>|wifi-priority <名> <n>|wifi-autoconnect <名> <yes|no>|wifi-info <名>|wifi-status|wifi-saved|hotspot-heal|factory-reset [net]|restart-hotspot|restart-web|reboot|shutdown|time-status|ntp <on|off>|timesync|tz <Asia/Shanghai>|sched-reboot <on|off> [HH:MM] [daily|weekly]|backup|logs <服务> [行数]|ping <主机>|net-info <网卡>|net-set <网卡> <IP> <掩码> <网关> <DNS> <static|dhcp>|wifi-restore|wifi-boot|fix-units|sys-upgrade-count|sys-update-check|sys-update|sys-update-status|sys-update-clean|boot-fw-status|boot-fw-update|fw-info|fw-url [地址]|fw-check|fw-online-update|fw-log [行数]}"; exit 2 ;;
esac
