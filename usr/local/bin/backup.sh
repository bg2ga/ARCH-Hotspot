#!/bin/bash
set -euo pipefail
D=/var/backups/mmdvm; mkdir -p "$D"; F="$D/arch-hotspot-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$F" /etc/MMDVM-*.ini /etc/DMRGateway.ini /etc/mmdvm /opt/fmo-bridge/config.json 2>/dev/null || true
echo "备份完成：$F"