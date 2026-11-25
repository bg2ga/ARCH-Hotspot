#!/bin/bash
set -u
MODE="${1:-check}"; MANIFEST="$(printf '%s' "${2:-https://hljrsa.cn:50113/releases/firmware/mmdvm-multi.json}" | tr -d '\r\n')"
BASE=/var/lib/mmdvm/firmware; BACKUP=/var/backups/mmdvm/firmware; LOCK=/run/mmdvm-fw-update.lock
ALLOW_HTTP="${ARCH_ALLOW_HTTP_UPDATE:-0}"
validate_url(){
  case "$1" in
    https://*) return 0 ;;
    http://*) [ "$ALLOW_HTTP" = "1" ] && return 0 ;;
  esac
  log STATUS=error; log MESSAGE='固件清单/下载地址默认必须使用 HTTPS；私有 HTTP 镜像需显式设置 ARCH_ALLOW_HTTP_UPDATE=1。'; return 1
}
mkdir -p "$BASE" "$BACKUP"; umask 077
current_version(){ if [ -s /etc/mmdvm/firmware-version ]; then head -1 /etc/mmdvm/firmware-version; elif [ -s /etc/mmdvm/install-state.conf ]; then awk -F= '/^VERSION=/{print $2;exit}' /etc/mmdvm/install-state.conf; else echo -; fi; }
log(){ printf '%s\n' "$*"; }
fetch_manifest(){
 validate_url "$MANIFEST" || return 1
 rm -rf "$BASE/tmp"; mkdir -p "$BASE/tmp"
 timeout 20 curl -4 -fsSL --retry 1 --connect-timeout 5 --max-time 15 -A 'ARCH-Hotspot/2.3' -o "$BASE/tmp/manifest.json" "$MANIFEST" || { log STATUS=error; log "MESSAGE=无法下载固件清单：$MANIFEST"; return 1; }
 python3 -m json.tool "$BASE/tmp/manifest.json" >/dev/null 2>&1 || { log STATUS=error; log MESSAGE='固件清单 JSON 格式错误'; return 1; }
 IFS=$'\t' read -r VERSION URL SHA256 SIZE NOTES < <(python3 - "$BASE/tmp/manifest.json" <<'PY'
import json,sys
x=json.load(open(sys.argv[1],encoding='utf-8'))
vals=[str(x.get(k,0) or '').replace('\t',' ').replace('\r',' ').replace('\n',' ') for k in ('version','url','sha256','size','notes')]
print('\t'.join(vals))
PY
)
}
health(){ systemctl is-active --quiet mmdvm-multi.service; }
backup_bin(){ local dst; dst="$BACKUP/MMDVM-Multi.$(date +%Y%m%d-%H%M%S)"; [ -f /usr/local/bin/MMDVM-Multi ] || return 0; install -m755 /usr/local/bin/MMDVM-Multi "$dst"; sha256sum "$dst">"$dst.sha256"; echo "$dst"; }
restore_bin(){ local b="$1"; [ -f "$b" ] || return 1; install -m755 "$b" /usr/local/bin/MMDVM-Multi; systemctl restart mmdvm-multi.service >/dev/null 2>&1 || true; sleep 4; health; }
exec 9>"$LOCK"; flock -n 9 || { log STATUS=busy; log MESSAGE='已有升级任务正在执行'; exit 2; }
if [ "$MODE" = check ]; then
 log "MANIFEST_URL=$MANIFEST"; log "CURRENT_VERSION=$(current_version)"; fetch_manifest || exit 0
 log "LATEST_VERSION=${VERSION:--}"; log "DOWNLOAD_URL=${URL:--}"; log "SHA256=${SHA256:--}"; log "SIZE=${SIZE:-0}"; log "NOTES=${NOTES:--}"
 [ -n "${URL:-}" ] && [ -n "${VERSION:-}" ] && [ "${VERSION}" != "$(current_version)" ] && log UPDATE_AVAILABLE=yes || log UPDATE_AVAILABLE=no; exit 0
fi
[ "$MODE" = update ] || { log "未知模式：$MODE"; exit 2; }
fetch_manifest || exit 1
[ -n "${URL:-}" ] && [ -n "${VERSION:-}" ] || { log STATUS=no_release; log MESSAGE='当前清单没有已发布固件。'; exit 0; }
[[ "$SHA256" =~ ^[0-9A-Fa-f]{64}$ ]] || { log STATUS=error; log MESSAGE='清单 SHA256 非法或缺失，拒绝安装。'; exit 1; }
case "$URL" in http://*|https://*) ;; *) log STATUS=error; log MESSAGE='固件 URL 必须使用 HTTP(S)。'; exit 1;; esac
PKG="$BASE/package.tmp"; EXTRACT="$BASE/extract"; rm -f "$PKG"; rm -rf "$EXTRACT"; mkdir -p "$EXTRACT"
timeout 900 curl -4 -fL --retry 2 --connect-timeout 8 --max-time 900 -A 'ARCH-Hotspot/2.3' -o "$PKG" "$URL" || { log STATUS=error; log MESSAGE='固件下载失败。'; exit 1; }
echo "$SHA256  $PKG" | sha256sum -c - || { log STATUS=error; log MESSAGE='SHA256 校验失败，已停止安装。'; exit 1; }
BACK="$(backup_bin | tail -1)"; RC=0; PKGTYPE=''
case "$PKG" in *.zip) unzip -q "$PKG" -d "$EXTRACT" || RC=$?; PKGTYPE=tree;; *.deb) dpkg -i "$PKG" >"$BASE/dpkg.log" 2>&1 || RC=$?; PKGTYPE=deb;; *) tar -xf "$PKG" -C "$EXTRACT" || RC=$?; PKGTYPE=tree;; esac
[ "$RC" -eq 0 ] || { log STATUS=error; log MESSAGE='固件包安装/解压失败。'; exit 1; }
if [ "$PKGTYPE" = tree ]; then
 BIN=$(find "$EXTRACT" -type f -name MMDVM-Multi -print -quit); SH=$(find "$EXTRACT" -type f -name '*.sh' -print -quit)
 if [ -n "$BIN" ]; then install -m755 "$BIN" /usr/local/bin/MMDVM-Multi
 elif [ -n "$SH" ]; then (cd "$(dirname "$SH")" && bash -euo pipefail "$(basename "$SH")") || RC=$?
 else log STATUS=error; log MESSAGE='固件包内没有 MMDVM-Multi 或安装脚本。'; exit 1; fi
fi
systemctl restart mmdvm-multi.service >/dev/null 2>&1 || RC=$?; sleep 4
if [ "$RC" -ne 0 ] || ! health; then log MESSAGE='升级后健康检查失败，开始回滚。'; [ -n "$BACK" ] && restore_bin "$BACK" >/dev/null 2>&1 || true; log STATUS=rollback; exit 1; fi
printf '%s\n' "$VERSION" >/etc/mmdvm/firmware-version; log STATUS=success; log "MESSAGE=在线固件升级完成：$VERSION"; exit 0
