#!/bin/bash
# web-fmo.sh —— FMO 桥接后端入口（Web 控制台调用，需 root）
set -Eeuo pipefail
exec python3 /usr/local/libexec/fmo-api.py "$@"
