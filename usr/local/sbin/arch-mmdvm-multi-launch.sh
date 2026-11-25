#!/usr/bin/env bash
set -euo pipefail
MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
CHANNELS="$(awk -F= '/^Channels=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' /etc/MMDVM-Multi.ini 2>/dev/null || true)"
RATE="$(awk -F= '/^SampleRate=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' /etc/MMDVM-Multi.ini 2>/dev/null || true)"
CHANNELS=${CHANNELS:-1}; RATE=${RATE:-0}
case "$MODEL" in
  *"Raspberry Pi 3"*|*"Compute Module 3"*|*"Zero 2"*) export ARCH_SX_TIMESTAMP=0 ;;
  *)
    if [ "$CHANNELS" -gt 4 ] || [ "$RATE" -ge 300000 ]; then export ARCH_SX_TIMESTAMP=0; else export ARCH_SX_TIMESTAMP=1; fi
    ;;
esac
exec /usr/local/bin/MMDVM-Multi /etc/MMDVM-Multi.ini
