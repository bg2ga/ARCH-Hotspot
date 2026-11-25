#!/bin/bash
# MMDVM-IQ web-manage.sh V20.2
# status 分支改为 Python 内嵌，避免 set -e 导致脚本提前退出
set -u

ACTION="${1:-}"

case "$ACTION" in
    status)
        exec /usr/local/libexec/mmdvm-status.py
        ;;

    status-quick)
        exec /usr/local/libexec/mmdvm-status.py quick
        ;;

    qso)
        # ★ 轻量：只输出通话记录（Live QSO 1 秒轮询用）
        exec /usr/local/libexec/mmdvm-status.py qso
        ;;

    ports)
        for p in 3334 3335 62031 62032 3200 4200 32010 42020 14020 14021 20010 20011; do
            if ss -lun 2>/dev/null | grep -qE ":$p\b"; then
                proc=$(ss -lunp 2>/dev/null | grep -E ":$p\b" | grep -oE 'users:\(\("[^"]+"' | sed 's/users:(("//;s/"//' | head -1 2>/dev/null || echo "未知")
                echo "$p|${proc:-未知}|LISTEN"
            else
                echo "$p|-|CLOSED"
            fi
        done
        ;;

    switch)
        MODE="${2:-}"
        case "$MODE" in
            DMR|D-STAR|YSF|P25|NXDN|FM|POCSAG) ;;
            *) echo "无效模式: $MODE" >&2; exit 2 ;;
        esac
        # ★ 多信道并行：启用某模式时绝不关闭其它通道。
        #   1) 规划里已分配该模式 -> 重载那条通道
        #   2) 规划里没有该模式 -> 占用一条空闲通道并加入运行路数
        #   3) 4 路都在跑 -> 提示用户到 Multi 页选择替换哪条通道
        python3 - "$MODE" <<'PYSW'
import json, os, subprocess, sys

MODE = sys.argv[1].upper()
PLAN = "/etc/mmdvm/channels.json"
CHSETUP = "/usr/local/libexec/channel-setup.py"
CHMODESH = "/usr/local/bin/channel-mode.sh"

def done(msg, code=0):
    print(msg)
    sys.exit(code)

if not os.path.exists(PLAN):
    done("规划文件不存在，请先运行 install-multi.sh", 1)
try:
    with open(PLAN, encoding="utf-8") as f:
        plan = json.load(f)
except Exception as e:
    done("规划文件读取失败: %s" % e, 1)

lst = plan.get("list") or []
try:
    r = subprocess.run(["/usr/local/libexec/arch-channel-limit.py"],
                       capture_output=True, text=True, timeout=8)
    hw_limit = max(1, min(7, int((r.stdout or "7").strip())))
except Exception:
    hw_limit = 7
running = max(1, min(hw_limit, int(plan.get("channels") or 1)))
plan["channels"] = running

def save_and_apply():
    with open(PLAN, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    if os.path.exists(CHSETUP):
        return subprocess.run([CHSETUP, "apply", json.dumps(plan)]).returncode == 0
    return False

def active_items():
    out = []
    for c in lst:
        try:
            n = int(c.get("n", 0))
        except Exception:
            continue
        if 1 <= n <= running:
            out.append(c)
    return out

active = active_items()

# 1) 当前已运行的通道里已经有这个模式，只重载该通道。
same = [c for c in active if str(c.get("mode", "")).upper() == MODE]
if same:
    tgt = int(same[0].get("n", 1))
    if os.path.exists(CHMODESH):
        rc = subprocess.run([CHMODESH, str(tgt), MODE]).returncode
        done("通道 %d 已重载 %s" % (tgt, MODE), 0 if rc == 0 else 1)
    done("找不到 channel-mode.sh", 1)

# 2) 单通道模式：切换就是替换当前这一路的模式，绝不阻挡。
if running == 1:
    cur = active[0] if active else None
    if cur is None:
        cur = next((c for c in lst if int(c.get("n", 0)) == 1), None)
    if cur is None:
        cur = {"n": 1, "mode": MODE,
               "rx": int(plan.get("center_rx", 434000000)),
               "tx": int(plan.get("center_tx", 439500000))}
        lst.append(cur)
        plan["list"] = lst
    cur["mode"] = MODE
    plan["channels"] = 1
    if save_and_apply():
        done("单通道已切换为 %s" % MODE)
    done("单通道切换失败，请查看 channel-setup 日志", 1)

# 3) 多通道模式：只在正在运行的通道里找空闲；历史未启用通道不算占用。
free = [c for c in active if str(c.get("mode", "NONE")).upper() in ("", "NONE")]
if not free:
    used = "/".join(str(c.get("mode", "NONE")) for c in active) or "NONE"
    done("当前 %d 路射频都在用（%s）。请先把一条运行中的通道设为 NONE，再切换 %s。"
         "未启用的历史通道不会计入占用。" % (running, used, MODE), 1)
cur = free[0]
cur["mode"] = MODE
plan["channels"] = running
if save_and_apply():
    done("已在通道 %d 启用 %s（当前 %d 路）" % (int(cur.get("n", 1)), MODE, running))
done("配置已写入，但应用失败，请查看 channel-setup 日志", 1)
PYSW
        ;;

    get-config)
        MODE="${2:-}"
        python3 - "$MODE" <<'PYCFG'
import sys, os
mode = sys.argv[1] if len(sys.argv) > 1 else ""

def read_ini(path):
    out = {}
    if not os.path.exists(path): return out
    current = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.rstrip("\n").strip()
            if not s or s.startswith(";") or s.startswith("#"): continue
            if s.startswith("[") and s.endswith("]"):
                current = s[1:-1]
                if current not in out: out[current] = {}
                continue
            if "=" in s and current is not None:
                k, v = s.split("=", 1)
                out[current][k.strip()] = v.strip()
    return out

def read_flat(path):
    out = {}
    if not os.path.exists(path): return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                out[k.strip()] = v.strip()
    return out

def get_sec(ini, sec):
    for k, v in ini.items():
        if k.lower() == sec.lower(): return v
    return {}

def emit(section, d, keys):
    print(f"=== {section} ===")
    for k in keys:
        if k in d: print(f"{k}={d[k]}")

host = read_ini("/etc/MMDVM-Host.ini")
g = get_sec(host, "General")
m = get_sec(host, "Modem")
info = get_sec(host, "Info")

if mode == "RF":
    emit("MMDVM_HOST", {**g, **m, **info},
         ["Callsign","Id","RXFrequency","TXFrequency","Duplex","Power"])
    iq = read_ini("/etc/MMDVM-IQ.ini")
    emit("MMDVM_IQ", get_sec(iq, "Soapy"), ["RxGain","TxGain","RxInvert","TxInvert"])
elif mode == "DMR":
    # ★ 多通道：DMR 不在通道 1 时，必须读实际 DMR 通道的 Host 配置
    _dmr_host = host
    try:
        import json as _json
        with open("/etc/mmdvm/channels.json", encoding="utf-8") as _f:
            _plan = _json.load(_f)
        _dmr_item = next((x for x in _plan.get("list", [])
                          if str(x.get("mode", "")).upper() == "DMR"), None)
        if _dmr_item:
            _dmr_host = read_ini("/etc/MMDVM-Host-%d.ini" % int(_dmr_item.get("n", 1)))
    except Exception:
        pass
    g = get_sec(_dmr_host, "General")
    dmr = get_sec(_dmr_host, "DMR")
    emit("MMDVM_HOST", {**g, **m, **info, **dmr},
         ["Callsign","Id","RXFrequency","TXFrequency","ColorCode","SelfOnly","CallHang","TXHang"])
    emit("DMR_NETWORK", get_sec(_dmr_host, "DMR Network"),
         ["Enable","GatewayAddress","GatewayPort","LocalAddress","LocalPort","Jitter","Slot1","Slot2"])
    gw = read_ini("/etc/DMRGateway.ini")
    _dmr_secs = [k for k in gw if k.lower().startswith("dmr network")]
    _sec = _dmr_secs[0] if _dmr_secs else "DMR Network 1"
    for _k in _dmr_secs:
        _d = get_sec(gw, _k)
        _blob = (str(_d.get("Name", "")) + " " + str(_d.get("Address", ""))).lower()
        if "brandmeister" in _blob:
            _sec = _k
            break
    else:
        if len(_dmr_secs) > 1:
            _sec = _dmr_secs[1]
    emit("DMRGATEWAY", get_sec(gw, _sec),
         ["Name","Id","Address","Port","Password","Enabled"])
elif mode == "D-STAR":
    emit("MMDVM_HOST", g, ["Callsign"])
    emit("MMDVM_HOST_DSTAR",
         {**get_sec(host, "D-Star"), **get_sec(host, "D-Star Network")},
         ["Module","SelfOnly","GatewayAddress","GatewayPort","LocalPort"])
    emit("IRCDDBGATEWAY", read_flat("/etc/ircddbgateway"),
         ["gatewayCallsign","repeaterCall1","repeaterBand1","ircddbUsername","ircddbEnabled",
          "ircddbHostname","reflector1","atStartup1","dplusEnabled","dextraEnabled",
          "dcsEnabled","ccsEnabled","xlxEnabled"])
elif mode == "YSF":
    emit("MMDVM_HOST", g, ["Callsign"])
    emit("MMDVM_HOST_YSF",
         {**get_sec(host, "System Fusion"), **get_sec(host, "System Fusion Network")},
         ["LoDev","SelfOnly","GatewayAddress","GatewayPort","LocalPort"])
    y = read_ini("/etc/YSFGateway.ini")
    emit("YSFGATEWAY",
         {**get_sec(y, "General"), **get_sec(y, "Network"), **get_sec(y, "YSF Network")},
         ["Callsign","Suffix","Id","RptAddress","RptPort","LocalAddress","LocalPort",
          "WiresXCommandPassthrough","WiresXMakeUpper","Startup","Port"])
elif mode == "P25":
    emit("MMDVM_HOST", g, ["Callsign"])
    emit("MMDVM_HOST_P25",
         {**get_sec(host, "P25"), **get_sec(host, "P25 Network")},
         ["SelfOnly","NAC","GatewayAddress","GatewayPort","LocalPort"])
    p = read_ini("/etc/P25Gateway.ini")
    emit("P25GATEWAY",
         {**get_sec(p, "General"), **get_sec(p, "Network")},
         ["Callsign","RptAddress","RptPort","LocalPort","Port","Static","RFHangTime","NetHangTime"])
elif mode == "NXDN":
    nxdn0 = read_ini("/etc/NXDNGateway.ini")
    emit("MMDVM_HOST", {**g, "Callsign": get_sec(nxdn0, "General").get("Callsign", g.get("Callsign","")), "Id": get_sec(nxdn0, "General").get("Id", g.get("Id",""))}, ["Callsign","Id"])
    emit("MMDVM_HOST_NXDN",
         {**get_sec(host, "NXDN"), **get_sec(host, "NXDN Network")},
         ["SelfOnly","RAN","Protocol","GatewayAddress","GatewayPort","LocalPort"])
    n = read_ini("/etc/NXDNGateway.ini")
    emit("NXDNGATEWAY",
         {**get_sec(n, "General"), **get_sec(n, "Network")},
         ["Callsign","RptProtocol","RptAddress","RptPort","LocalPort","Port","Static"])
elif mode == "FM":
    emit("MMDVM_HOST", g, ["Callsign"])
    emit("MMDVM_HOST_FM", get_sec(host, "FM"),
         ["Enable","CTCSSFrequency","CTCSSHighThreshold","CTCSSLowThreshold",
          "CTCSSLevel","KerchunkTime","HangTime","AccessMode"])
elif mode == "POCSAG":
    emit("MMDVM_HOST", g, ["Callsign"])
    emit("MMDVM_HOST_POCSAG", get_sec(host, "POCSAG"), ["Enable","Frequency"])
PYCFG
        ;;

    save-rf|save-dmr|save-ysf|save-p25|save-nxdn|save-dstar|save-fm|save-pocsag)
        MODE_UPPER="${ACTION#save-}"
        case "$MODE_UPPER" in
            rf) MODE_ARG="RF" ;;
            dmr) MODE_ARG="DMR" ;;
            ysf) MODE_ARG="YSF" ;;
            p25) MODE_ARG="P25" ;;
            nxdn) MODE_ARG="NXDN" ;;
            dstar) MODE_ARG="D-STAR" ;;
            fm) MODE_ARG="FM" ;;
            pocsag) MODE_ARG="POCSAG" ;;
        esac
        exec /usr/local/libexec/mmdvm-save.py "$MODE_ARG" "${@:2}"
        ;;

    logs)
        SVC="${2:-mmdvmhost}"
        case "$SVC" in
            mmdvm-iq|mmdvmhost|dmrgateway|ysfgateway|p25gateway|nxdngateway|ircddbgatewayd|mosquitto|mmdvm-web|mmdvm-multi|fmo-bridge) ;;
            *) echo "无效服务" >&2; exit 2 ;;
        esac
        journalctl -u "$SVC.service" -n "${3:-80}" --no-pager 2>&1 || echo "无法获取 $SVC 日志"
        ;;

    backup) exec /usr/local/bin/backup.sh ;;
    diagnose) exec /usr/local/bin/diagnose.sh ;;

    update-db)
        DB="${2:-dmrid}"
        if [ "$DB" = "dmrid" ]; then
            MMDVM_INTERNAL=1 exec /usr/local/bin/update-database.sh dmrid 9>&-
        elif [ "$DB" = "hosts" ]; then
            MMDVM_INTERNAL=1 exec /usr/local/bin/update-database.sh hosts 9>&-
        elif [ "$DB" = "all" ]; then
            MMDVM_INTERNAL=1 /usr/local/bin/update-database.sh hosts 9>&- 2>&1 || true
            MMDVM_INTERNAL=1 /usr/local/bin/update-database.sh dmrid 9>&- 2>&1 || true
            echo "全量同步完成"
        else
            exec /usr/local/bin/update-database.sh "$DB" 9>&-
        fi
        ;;

    bm-list)
        cat /etc/mmdvm/bm-masters.json 2>/dev/null || echo "[]"
        ;;

    bm-test)
        HOST="${2:-}"
        [ -n "$HOST" ] || { echo "需要指定主机" >&2; exit 2; }
        python3 - "$HOST" <<'PYBM'
import sys, socket, subprocess, re, json
host = sys.argv[1]
r = {"host": host, "dns": False, "loss": "N/A", "rtt": "N/A"}
try: r["dns"] = bool(socket.getaddrinfo(host, None))
except: pass
try:
    p = subprocess.run(["ping","-c","4","-W","1",host],
                       capture_output=True, text=True, timeout=8)
    t = p.stdout + p.stderr
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)% packet loss", t)
    if m: r["loss"] = f"{m.group(1)}%"
    m = re.search(r"(?:=|,)\s*[\d.]+/([\d.]+)/", t)
    if m: r["rtt"] = f"{m.group(1)} ms"
except: pass
print(json.dumps(r))
PYBM
        ;;

    restart)
        # 多信道架构：mmdvmhost / mmdvm-iq 是实例化单元 mmdvmhost@N / mmdvm-iq@N
        rest_channels(){
            local base="$1" n
            for n in 1 2 3 4 5; do
                [ -f "/etc/MMDVM-Host-$n.ini" ] || continue
                systemctl restart --no-block "$base@$n.service"
            done
        }
        case "${2:-}" in
            all)
                systemctl restart --no-block mmdvm-multi.service
                rest_channels mmdvm-iq
                rest_channels mmdvmhost
                systemctl restart --no-block dmrgateway.service ;;
            mmdvmhost)  rest_channels mmdvmhost ;;
            mmdvm-iq)   rest_channels mmdvm-iq ;;
            mmdvm-multi|dmrgateway|ysfgateway|p25gateway|nxdngateway|ircddbgatewayd)
                systemctl restart --no-block "$2.service" ;;
            *) echo "无效服务" >&2; exit 2 ;;
        esac
        echo "已提交重启（后台执行）"
        ;;

    *)
        echo "用法: $0 {status|ports|switch|get-config|save-XXX|logs|backup|diagnose|update-db|bm-list|bm-test|restart}"
        exit 2 ;;
esac
