#!/bin/bash
set -euo pipefail
exec /usr/local/libexec/channel-setup.py set "$1" "$2"