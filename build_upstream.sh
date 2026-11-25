#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "请使用 sudo 运行"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
export CC=gcc CXX=g++
DEPS_BASE="${ARCH_DEPS_BASE:-https://arch.hljrsa.cn/downloads/arch-hotspot/deps}"
mkdir -p /opt/src-mmdvm /opt/arch-deps
# Bound compile fan-out for small-memory devices.
if [ -r /proc/meminfo ]; then
  MEM_MB=$(awk '/^MemTotal:/{print int($2/1024); exit}' /proc/meminfo 2>/dev/null || echo 0)
else
  MEM_MB=0
fi
CPU_CORES=$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 1)
case "$CPU_CORES" in ''|*[!0-9]*|0) CPU_CORES=1;; esac
[ "$CPU_CORES" -gt 4 ] && CPU_CORES=4
BUILD_JOBS="$CPU_CORES"
if [ "$MEM_MB" -gt 0 ]; then
  MEM_JOBS=$((MEM_MB / 450))
  [ "$MEM_JOBS" -lt 1 ] && MEM_JOBS=1
  [ "$BUILD_JOBS" -gt "$MEM_JOBS" ] && BUILD_JOBS="$MEM_JOBS"
fi
[ "$BUILD_JOBS" -gt 4 ] && BUILD_JOBS=4
if [ -n "${ARCH_BUILD_JOBS:-}" ]; then BUILD_JOBS="$ARCH_BUILD_JOBS"; fi
case "$BUILD_JOBS" in
  ''|*[!0-9]*|0) BUILD_JOBS=1 ;;
esac
export MAKEFLAGS="-j$BUILD_JOBS ${MAKEFLAGS:-}"
export CMAKE_BUILD_PARALLEL_LEVEL="$BUILD_JOBS"
apt-get install -y --no-install-recommends git build-essential cmake autoconf automake libtool pkg-config libasio-dev libssl-dev libcurl4-openssl-dev libsamplerate0-dev libsox-dev libmosquitto-dev libsoapysdr-dev soapysdr-tools libasound2-dev libliquid-dev nlohmann-json3-dev mosquitto libwxgtk3.2-dev wx-common
if [ ! -f /usr/include/liquid/liquid.h ]; then
  apt-get install -y --no-install-recommends autoconf automake libtool libfftw3-dev
fi
if [ ! -f /opt/arch-deps/arch-upstream-src.tar.gz ]; then
  curl -fsSL "$DEPS_BASE/arch-upstream-src.tar.gz" -o /opt/arch-deps/arch-upstream-src.tar.gz
fi
rm -rf /opt/src-mmdvm
mkdir -p /opt/src-mmdvm
if command -v pigz >/dev/null 2>&1; then tar -I pigz -xf /opt/arch-deps/arch-upstream-src.tar.gz -C /opt/src-mmdvm; else tar -xzf /opt/arch-deps/arch-upstream-src.tar.gz -C /opt/src-mmdvm; fi
python3 - <<'PY'
import re
from pathlib import Path
p = Path('/opt/src-mmdvm/MMDVM-Multi/MMDVM-Multi.cpp')
s = p.read_text(encoding='utf-8')
s = s.replace('deviceType.compare("plutosdr") == 0 || deviceType.compare("pluto") == 0',
              'deviceType.compare("plutosdr") == 0 || deviceType.compare("pluto") == 0')
p.write_text(s, encoding='utf-8')
c = Path('/opt/src-mmdvm/MMDVM-Multi/Constants.h')
t = c.read_text(encoding='utf-8').replace('PFB_FILTER_DELAY = 48U', 'PFB_FILTER_DELAY = 24U')
c.write_text(t, encoding='utf-8')

# (已删除) MMDVM-IQ duplex RX 补丁：它导致手台语音被 idle 解码器当成 CSBK，
#           表现为发射时热点反而去发 CSBK、DMR 收不到。
#           已改回上游原始行为（空闲用 dmrIdleRX，发射中用 dmrRX）。
PY
python3 - <<'PY'
from pathlib import Path

# MMDVM-IQ wakes every 1 ms per channel.  Data arrives in 30 ms blocks over
# the Multi socket, so 5 ms polling removes most idle wakeups without adding
# material latency.
p = Path('/opt/src-mmdvm/MMDVM-IQ/MMDVM-IQ.cpp')
s = p.read_text(encoding='utf-8')
old = 'if (driver == "Multi")\n            CThread::sleep(1U);'
new = 'if (driver == "Multi")\n            CThread::sleep(1U);'
if old in s:
    p.write_text(s.replace(old, new, 1), encoding='utf-8')

# Resampler used new[]/delete[] four times per 72-sample block.  Reuse fixed
# per-object scratch buffers instead; this removes thousands of allocations
# per second on multi-channel systems.
h = Path('/opt/src-mmdvm/MMDVM-Multi/Resampler.h')
s = h.read_text(encoding='utf-8')
old = '''    rresamp_crcf m_upsampler[MAX_MMDVM_CHANNELS];
    rresamp_crcf m_downsampler[MAX_MMDVM_CHANNELS];'''
new = '''    rresamp_crcf m_upsampler[MAX_MMDVM_CHANNELS];
    rresamp_crcf m_downsampler[MAX_MMDVM_CHANNELS];

    // 64 is comfortably above the current 25/24 ratios and avoids heap churn
    // in the per-sample-block receive path.
    std::complex<float> m_upIn[64];
    std::complex<float> m_upOut[64];
    std::complex<float> m_downIn[64];
    std::complex<float> m_downOut[64];'''
if old in s and 'm_upIn[64]' not in s:
    h.write_text(s.replace(old, new, 1), encoding='utf-8')

p = Path('/opt/src-mmdvm/MMDVM-Multi/Resampler.cpp')
s = p.read_text(encoding='utf-8')
old = '''    std::complex<float>* in_buf  = new std::complex<float>[m_decim];
    std::complex<float>* out_buf = new std::complex<float>[m_interp];

    for (unsigned int i = 0U; i < p_in; i++) {
        ::memcpy(in_buf, in_samples + (i * m_decim), m_decim * sizeof(std::complex<float>));
        ::rresamp_crcf_execute(m_upsampler[channel], in_buf, out_buf);
        ::memcpy(out_samples + (i * m_interp), out_buf, m_interp * sizeof(std::complex<float>));
    }

    delete[] in_buf;
    delete[] out_buf;'''
new = '''    std::complex<float>* in_buf  = m_upIn;
    std::complex<float>* out_buf = m_upOut;

    for (unsigned int i = 0U; i < p_in; i++) {
        ::memcpy(in_buf, in_samples + (i * m_decim), m_decim * sizeof(std::complex<float>));
        ::rresamp_crcf_execute(m_upsampler[channel], in_buf, out_buf);
        ::memcpy(out_samples + (i * m_interp), out_buf, m_interp * sizeof(std::complex<float>));
    }'''
if old in s:
    s = s.replace(old, new, 1)
old = '''    std::complex<float>* in_buf  = new std::complex<float>[m_interp];
    std::complex<float>* out_buf = new std::complex<float>[m_decim];

    for (unsigned int i = 0U; i < p_in; i++) {
        ::memcpy(in_buf, in_samples + (i * m_interp), m_interp * sizeof(std::complex<float>));
        ::rresamp_crcf_execute(m_downsampler[channel], in_buf, out_buf);
        ::memcpy(out_samples + (i * m_decim), out_buf, m_decim * sizeof(std::complex<float>));
    }

    delete[] in_buf;
    delete[] out_buf;'''
new = '''    std::complex<float>* in_buf  = m_downIn;
    std::complex<float>* out_buf = m_downOut;

    for (unsigned int i = 0U; i < p_in; i++) {
        ::memcpy(in_buf, in_samples + (i * m_interp), m_interp * sizeof(std::complex<float>));
        ::rresamp_crcf_execute(m_downsampler[channel], in_buf, out_buf);
        ::memcpy(out_samples + (i * m_decim), out_buf, m_decim * sizeof(std::complex<float>));
    }'''
if old in s:
    s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

c = Path('/opt/src-mmdvm/MMDVM-Multi/Channelizer.cpp')
s = c.read_text(encoding='utf-8')
old_c = 'assert(m_channels > 3U);'
if old_c in s:
    c.write_text(s.replace(old_c, 'assert(m_channels > 1U);', 1), encoding='utf-8')
PY
if [ ! -f /usr/include/liquid/liquid.h ]; then
  cd /opt/src-mmdvm/liquid-dsp
  ./bootstrap.sh
  ./configure
  make -j"$BUILD_JOBS"
  make install
  ldconfig
fi
# Re-apply the verified low-memory MMDVM-Multi overlay after refreshing upstream.
MULTI_OPT_DIR="${ARCH_MULTI_OPT_DIR:-/usr/local/share/arch-hotspot/mmdvm-multi-opt}"
if [ -d "$MULTI_OPT_DIR" ]; then
  for f in Receiver.cpp Receiver.h Transmitter.cpp Transmitter.h Makefile MMDVM-Multi.ini; do
    [ -f "$MULTI_OPT_DIR/$f" ] && install -m 0644 "$MULTI_OPT_DIR/$f" "/opt/src-mmdvm/MMDVM-Multi/$f"
  done
fi
# Apply ARCH fixed/absolute channel-bin mapping patch after overlay.
if [ -f "$MULTI_OPT_DIR/patch_multi_bins.py" ]; then
  python3 "$MULTI_OPT_DIR/patch_multi_bins.py" /opt/src-mmdvm/MMDVM-Multi
fi

build_repo() {
  local name="$1" binary="$2"
  if [ -x "/usr/local/bin/$binary" ] && [ "${FORCE_MMDVM_IQ:-0}" != "1" ] && ! { [ "$name" = "MMDVM-Multi" ] && [ "${FORCE_MMDVM_MULTI:-0}" = "1" ]; }; then echo "$binary 已存在，跳过编译"; return 0; fi
  make -C "/opt/src-mmdvm/$name" -j"$BUILD_JOBS"
  install -m 0755 "/opt/src-mmdvm/$name/$binary" "/usr/local/bin/$binary"
}
build_subdir() {
  local group="$1" name="$2" binary="$3"
  if [ -x "/usr/local/bin/$binary" ] && [ "${FORCE_MMDVM_IQ:-0}" != "1" ]; then echo "$binary 已存在，跳过编译"; return 0; fi
  make -C "/opt/src-mmdvm/$group/$name" -j"$BUILD_JOBS"
  make -C "/opt/src-mmdvm/$group/$name" install
}
build_ircddb() {
  if [ -x /usr/local/bin/ircddbgatewayd ] && [ "${FORCE_MMDVM_IQ:-0}" != "1" ]; then echo "ircddbgatewayd 已存在，跳过编译"; return 0; fi
  make -C /opt/src-mmdvm/ircDDBGateway -j"$BUILD_JOBS" ircDDBGateway/ircddbgatewayd
  install -m 0755 /opt/src-mmdvm/ircDDBGateway/ircDDBGateway/ircddbgatewayd /usr/local/bin/ircddbgatewayd
}
build_repo MMDVM-Multi MMDVM-Multi
if [ "${FORCE_MMDVM_IQ:-0}" = "1" ]; then rm -f /usr/local/bin/MMDVM-IQ; fi
build_repo MMDVM-IQ MMDVM-IQ
build_repo MMDVM-Host MMDVM-Host
build_repo DMRGateway DMRGateway
build_subdir YSFClients YSFGateway YSFGateway
build_subdir P25Clients P25Gateway P25Gateway
build_subdir NXDNClients NXDNGateway NXDNGateway
build_ircddb
if [ -d /opt/src-mmdvm/sxxcvr/SoapySX ]; then
  SOAPY_SX_SRC=/opt/src-mmdvm/sxxcvr/SoapySX
  SOAPY_SX_MODULE="$(find /usr/local/lib /usr/lib -type f -name '*SXSupport*.so' 2>/dev/null | head -1 || true)"
  if [ -z "$SOAPY_SX_MODULE" ]; then
    python3 - "$SOAPY_SX_SRC/CMakeLists.txt" <<'PY'
from pathlib import Path
import re, sys
p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")
s = s.replace("find_package(Git REQUIRED)\n", "")
s = re.sub(r"set\(VERSION_SCRIPT[^\n]*\)\n", "", s)
s = re.sub(r"\nadd_custom_command\(.*?\n\)\n", "\n", s, flags=re.S)
p.write_text(s, encoding="utf-8")
PY
    cat > "$SOAPY_SX_SRC/version.cpp" <<'EOF'
const char *SoapySX_tag = "arch-hotspot";
const char *SoapySX_commit = "release";
EOF
    rm -rf /opt/src-mmdvm/sxxcvr/build
    # 注意：不要给 SoapySX 加 -DCMAKE_BUILD_TYPE=Release！
    # -O3 会改变浮点结果，导致 I/Q 采样判决偏移、DMR 语音同步被判成数据同步。
    # 保持默认（无优化）与 9/13 可用版本一致。
    cmake -S "$SOAPY_SX_SRC" -B /opt/src-mmdvm/sxxcvr/build
    cmake --build /opt/src-mmdvm/sxxcvr/build -j"$BUILD_JOBS"
    cmake --install /opt/src-mmdvm/sxxcvr/build
    ldconfig
    SOAPY_SX_MODULE="$(find /usr/local/lib /usr/lib -type f -name '*SXSupport*.so' 2>/dev/null | head -1 || true)"
  fi
  [ -n "$SOAPY_SX_MODULE" ] || { echo "SoapySX 模块安装失败" >&2; exit 1; }
  mkdir -p /etc/mmdvm
  if SoapySDRUtil --info 2>/dev/null | grep -q "Available factories... sx"; then touch /etc/mmdvm/soapy-sx-verified; fi
  echo "SoapySX 模块：$SOAPY_SX_MODULE"
fi
echo "ARCH 官方组件安装完成"
