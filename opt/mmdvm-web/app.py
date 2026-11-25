#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import gzip, subprocess, os, re, json, socket, glob, signal, threading, time, urllib.request, urllib.parse
from flask import Flask, render_template_string, request, jsonify

try:  # CONSOLE_NEW
    import sys as _sys_cn
    if "/opt/mmdvm-web" not in _sys_cn.path:
        _sys_cn.path.insert(0, "/opt/mmdvm-web")
    from console_new import CONSOLE_STANDALONE
except Exception:
    CONSOLE_STANDALONE = '<h3>控制台模块缺失</h3>'


try:  # RFMODE_SAFE
    import sys as _sys_rfms
    if "/opt/mmdvm-web" not in _sys_rfms.path:
        _sys_rfms.path.insert(0, "/opt/mmdvm-web")
    from rfmode_standalone import RFMODE_STANDALONE
except Exception:
    RFMODE_STANDALONE = '<h3>射频模式配置页模块缺失</h3>'


try:
    from fmo_page import FMO_HTML
except Exception:
    FMO_HTML = '<h3>FMO page module missing</h3>'



app = Flask(__name__)


@app.after_request
def _arch_gzip_response(resp):
    try:
        if resp.status_code != 200 or resp.direct_passthrough:
            return resp
        if resp.headers.get('Content-Encoding'):
            return resp
        if 'gzip' not in (request.headers.get('Accept-Encoding') or '').lower():
            return resp
        if resp.mimetype not in ('text/html', 'application/json', 'text/css', 'application/javascript', 'text/javascript', 'text/plain'):
            return resp
        raw = resp.get_data()
        if len(raw) < 1024:
            return resp
        resp.set_data(gzip.compress(raw, compresslevel=5))
        resp.headers['Content-Encoding'] = 'gzip'
        resp.headers['Vary'] = 'Accept-Encoding'
        resp.headers['Content-Length'] = str(len(resp.get_data()))
    except Exception:
        pass
    return resp
app.config['MAX_CONTENT_LENGTH'] = 256 * 1024


@app.after_request
def _no_store(resp):
    """★ 控制台页面与接口一律禁用缓存：
    否则改完页面后浏览器还拿旧的（新菜单/新页面看不到）。"""
    try:
        _ct = resp.headers.get('Content-Type', '') or ''
        if _ct.startswith('text/html') or _ct.startswith('application/json'):
            resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            resp.headers['Pragma'] = 'no-cache'
            resp.headers.pop('Expires', None)
    except Exception:
        pass
    return resp

def run_cmd(cmd_list, timeout=45):
    """执行命令并捕获输出。

    ★ 超时必须杀掉整个进程组：只杀直接子进程（sudo）时，
    被 sudo 拉起的 systemctl 仍持有输出管道，subprocess.run 会
    一直等到管道 EOF —— 表现为网页一直转圈、永不返回。
    """
    try:
        p = subprocess.Popen(cmd_list, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True,
                             start_new_session=True)
    except Exception as e:
        return {"ok": False, "code": -1, "stdout": "", "stderr": str(e)}
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        try:
            out, err = p.communicate(timeout=5)
        except Exception:
            out, err = "", ""
        return {"ok": False, "code": 124,
                "stdout": (out or "").strip(),
                "stderr": "命令执行超时（已终止）"}
    return {"ok": p.returncode == 0, "code": p.returncode,
            "stdout": (out or "").strip(), "stderr": (err or "").strip()}


# ============ DMR ID 数据库（ID ↔ 呼号） ============
_DMR_IDS = None
_DMR_IDS_MTIME = 0
_DMR_CALL_BY_ID = {}
_RADIOID_MEM_CACHE = {}
_RADIOID_PENDING = set()
_RADIOID_LOCK = threading.Lock()
_RADIOID_CACHE_FILE = "/opt/mmdvm-web/data/radioid_cache.json"

def _load_dmr_ids():
    """加载 /usr/local/etc/DMRIds.dat 到内存（带 mtime 检查）"""
    global _DMR_IDS, _DMR_IDS_MTIME, _DMR_CALL_BY_ID
    path = "/usr/local/etc/hosts/DMRIds.dat"
    try:
        import os
        if not os.path.exists(path):
            _DMR_IDS = {}
            return _DMR_IDS
        mtime = os.path.getmtime(path)
        if _DMR_IDS is not None and mtime == _DMR_IDS_MTIME:
            return _DMR_IDS
        ids = {}
        by_id = {}
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 2:
                    dmr_id = parts[0].strip()
                    call = parts[1].strip().upper()
                    if call and dmr_id:
                        ids[call] = dmr_id
                        by_id[dmr_id] = call
        _DMR_IDS = ids
        _DMR_CALL_BY_ID = by_id
        _DMR_IDS_MTIME = mtime
        return ids
    except Exception:
        _DMR_IDS = {}
        _DMR_CALL_BY_ID = {}
        return _DMR_IDS

def lookup_dmr_id(call):
    """按呼号查 DMR ID"""
    if not call or call == '-':
        return ''
    ids = _load_dmr_ids()
    return ids.get(call.upper(), '')

def _load_radioid_cache():
    with _RADIOID_LOCK:
        if _RADIOID_MEM_CACHE:
            return _RADIOID_MEM_CACHE
        out = {}
        for path in (_RADIOID_SEED_FILE, _RADIOID_CACHE_FILE):
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    for k, v in d.items():
                        call = str(v or "").strip().upper()
                        if call:
                            out[str(k).strip()] = call
            except Exception:
                pass
        _RADIOID_MEM_CACHE.clear()
        _RADIOID_MEM_CACHE.update(out)
        return _RADIOID_MEM_CACHE


def _save_radioid_cache():
    try:
        os.makedirs(os.path.dirname(_RADIOID_CACHE_FILE), exist_ok=True)
        tmp = _RADIOID_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_RADIOID_MEM_CACHE, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _RADIOID_CACHE_FILE)
    except Exception:
        pass


def _fetch_radioid_call(dmr_id):
    try:
        url = ("https://database.radioid.net/api/dmr/user/?id=" +
               urllib.parse.quote(str(dmr_id)))
        req = urllib.request.Request(url, headers={"User-Agent": "ARCH-Hotspot/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        rows = data.get("results") or []
        if rows:
            call = str(rows[0].get("callsign") or "").strip().upper()
            if call:
                with _RADIOID_LOCK:
                    _RADIOID_MEM_CACHE[str(dmr_id)] = call
                    _save_radioid_cache()
    except Exception:
        pass
    finally:
        with _RADIOID_LOCK:
            _RADIOID_PENDING.discard(str(dmr_id))


def lookup_dmr_call(dmr_id):
    """数字 DMR/P25 ID 查呼号；未命中时后台查询 RadioID.net 并缓存。"""
    key = str(dmr_id or "").strip()
    if not key.isdigit():
        return ""
    try:
        _load_dmr_ids()
    except Exception:
        pass
    call = _DMR_CALL_BY_ID.get(key, "")
    if call:
        return call
    cache = _load_radioid_cache()
    call = cache.get(key, "")
    if call:
        return call
    with _RADIOID_LOCK:
        if key in _RADIOID_PENDING or len(_RADIOID_PENDING) >= 10:
            return ""
        _RADIOID_PENDING.add(key)
    threading.Thread(target=_fetch_radioid_call, args=(key,), daemon=True).start()
    return ""


def parse_qso_line(line):
    """解析 MMDVM-Host 的 QSO 日志行（兼容 DMR / YSF / D-STAR）"""
    # 多信道：状态脚本会给每条前面加 "[CH2] "，先取出通道号
    ch = ''
    mch = re.search(r'\[CH(\d+)\]', line)   # FIX_FM_CH
    if mch:
        ch = 'CH' + mch.group(1)
        line = line[:mch.start()] + line[mch.end():]   # FIX_FM_CH 只删标记，保留时间戳

    # 提取时间（★ MMDVM 日志里的时间是 UTC，必须换算成本机时区，
    #   否则看板时间会比实际少 8 小时。用 time.timezone（本机 UTC 偏移,秒,西正东负）换算，
    #   对东八区 = UTC+8，且不受夏令时影响（中国无夏令时）。）
    times = re.findall(r'(\d{2}:\d{2}:\d{2})', line)
    t_str = times[-1] if times else ''
    if t_str:
        try:
            import time as _t
            _h, _m, _s = [int(_x) for _x in t_str.split(':')]
            _loc = (_h * 3600 + _m * 60 + _s - int(_t.timezone)) % 86400
            t_str = "%02d:%02d:%02d" % (_loc // 3600, (_loc % 3600) // 60, _loc % 60)
        except Exception:
            pass

    # 检测模式
    low = line.lower()
    is_fm_end = '网络流结束' in line
    end_event = is_fm_end or ('end of voice' in low) or ('end of transmission' in low) or ('watchdog' in low and 'expired' in low)
    if is_fm_end:
        mode = 'FM'
    elif 'd-star' in low or 'dstar' in low:
        mode = 'D-Star'
    elif 'dmr slot 1' in low:
        mode = 'DMR S1'
    elif 'dmr slot 2' in low:
        mode = 'DMR S2'
    elif 'dmr' in low:
        mode = 'DMR'
    elif 'ysf' in low:
        mode = 'YSF'
    elif 'p25' in low:
        mode = 'P25'
    elif 'nxdn' in low:
        mode = 'NXDN'
    elif 'pocsag' in low:
        mode = 'POCSAG'
    elif 'fm' in low:
        mode = 'FM'
    else:
        mode = 'UNKNOWN'

    # 方向：RF 还是 Net
    if 'received network' in low:
        src = 'Net'
    elif 'received rf' in low:
        src = 'RF'
    else:
        src = 'Local'

    # 提取呼号
    call = '-'
    m = re.search(r'from\s+([A-Z0-9][A-Z0-9/\-]+)', line, re.I)
    if m:
        call = m.group(1).strip()
    original_id = call if call.isdigit() else ""
    if original_id:
        resolved = lookup_dmr_call(original_id)
        if resolved:
            call = resolved

    # 提取目标
    target = '-'
    m = re.search(r'to\s+((?:TG|PC|All)\s+\d+)', line, re.I)
    if m:
        target = m.group(1).strip()
    else:
        # ★ YSF 的 "to DG-ID 0 at <呼号>" 单独识别，避免目标显示成 '-'
        m = re.search(r'to\s+(DG-ID\s+\d+)', line, re.I)
        if m:
            target = m.group(1).strip()
        else:
            m = re.search(r'to\s+([A-Z0-9][A-Z0-9/\-]*?)(?:\s*,\s*\d|\.|\s*$)', line, re.I)
            if m:
                target = m.group(1).strip()

    # 提取时长
    duration = '0.0s' if is_fm_end else '-'
    m = re.search(r'(\d+(?:\.\d+)?)\s*seconds', line, re.I)
    if m:
        duration = f"{float(m.group(1)):.1f}s"
    elif ('header' in low
          or 'received network transmission' in low
          or 'received rf voice transmission' in low
          or 'received network data from' in low):
        # ★ YSF / P25 / NXDN 网络侧没有 header 行，只有“开始行”，
        #   这里一并判为“通话中”，否则这些模式在通联列表里不会变红。
        duration = '通话中'

    # 提取 BER
    ber = '-'
    m = re.search(r'BER[:\s]+(\d+(?:\.\d+)?)%?', line, re.I)
    if m:
        ber = f"{m.group(1)}%"

    # 提取 DMR ID
    dmr_id = original_id
    if not dmr_id:
        try:
            dmr_id = lookup_dmr_id(call)
        except Exception:
            pass

    return {
        'time': t_str,
        'mode': mode,
        'ch': ch,
        'src': src,
        'call': call,
        'target': target,
        'duration': duration,
        'ber': ber,
        'id': dmr_id,
        '_watchdog': ('watchdog' in low and 'expired' in low),
        '_end': end_event,
    }

def read_channel_plan():
    try:
        with open('/etc/mmdvm/channels.json', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def plan_primary():
    plan = read_channel_plan()
    items = []
    for item in plan.get('list', []):
        try:
            if str(item.get('mode', '')).upper() not in ('', 'NONE'):
                items.append(item)
        except Exception:
            pass
    items.sort(key=lambda x: int(x.get('n', 0)))
    return plan, (items[0] if items else None)


def read_ini_value(path, section, key):
    try:
        if not os.path.exists(path):
            return None
        cur = None
        tsec = section.lower()
        tkey = key.lower()
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith(";") or s.startswith("#"):
                    continue
                if s.startswith("[") and s.endswith("]"):
                    cur = s[1:-1].lower()
                    continue
                if "=" in s and cur == tsec:
                    k, v = s.split("=", 1)
                    if k.strip().lower() == tkey:
                        return v.strip()
    except Exception:
        pass
    return None

def read_freq_hz():
    p = "/etc/MMDVM-Host.ini"
    rxv = read_ini_value(p, "Modem", "RXFrequency") or read_ini_value(p, "Info", "RXFrequency")
    txv = read_ini_value(p, "Modem", "TXFrequency") or read_ini_value(p, "Info", "TXFrequency")
    try: rx = int(rxv) if rxv else 0
    except: rx = 0
    try: tx = int(txv) if txv else 0
    except: tx = 0
    return rx, tx

def read_callsign_id():
    p = "/etc/MMDVM-Host.ini"
    return (read_ini_value(p, "General", "Callsign") or "",
            read_ini_value(p, "General", "Id") or "0")


def read_host_ini_via_sudo():
    """读取 /etc/MMDVM-Host.ini；该文件为 0644，无需 sudo cat。

    保留原函数名以兼容既有调用点，同时避免 mmdvm-web 用户反复触发
    未授权的 sudo 日志。
    """
    try:
        with open('/etc/MMDVM-Host.ini', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception:
        return None

def parse_host_ini(content):
    """从 INI 内容解析 rx_hz/tx_hz/callsign/id"""
    result = {'rx_hz': 0, 'tx_hz': 0, 'callsign': '', 'id': '0'}
    if not content:
        return result
    cur = None
    for line in content.split('\n'):
        s = line.strip()
        if not s or s.startswith(';') or s.startswith('#'):
            continue
        if s.startswith('[') and s.endswith(']'):
            cur = s[1:-1].lower()
            continue
        if '=' not in s:
            continue
        k, v = s.split('=', 1)
        k = k.strip().lower()
        v = v.strip()
        if cur == 'modem':
            if k == 'rxfrequency':
                try: result['rx_hz'] = int(v)
                except: pass
            elif k == 'txfrequency':
                try: result['tx_hz'] = int(v)
                except: pass
        elif cur == 'info' and result['rx_hz'] == 0:
            if k == 'rxfrequency':
                try: result['rx_hz'] = int(v)
                except: pass
            elif k == 'txfrequency':
                try: result['tx_hz'] = int(v)
                except: pass
        elif cur == 'general':
            if k == 'callsign' and not result['callsign']:
                result['callsign'] = v
            elif k == 'id' and result['id'] == '0':
                result['id'] = v
    return result

def _merge_qso(items):
    out=[]
    for idx,it in enumerate(items):
        if it.get('_watchdog'): continue
        if str(it.get('duration'))!='通话中': out.append(it); continue
        base=(it.get('ch'),it.get('mode'),it.get('slot'),it.get('call'))
        ended=any((not x.get('_watchdog')) and str(x.get('duration'))!='通话中' and
                  ((x.get('ch'),x.get('mode'),x.get('slot'),x.get('call'))==base or
                   (it.get('mode')=='FM' and x.get('_end'))) for x in items[:idx])
        if not ended: out.append(it)
    return out


# ★ 通话记录短缓存（0.6 秒）：前端 0.5 秒轮询时会把重复请求折叠成一次读取，
#   否则每次都要起一个 journalctl + python，Pi 上很容易把 CPU 打上去。
#   只缓存“读日志”的结果，红色高亮最多延迟 0.6 秒，肉眼无感。
_QSO_CACHE = {'t': 0.0, 'payload': None}
_QSO_LOCK = threading.Lock()
_QSO_TTL = 0.5


_QSO_HISTORY = {}
_QSO_HISTORY_TTL = 60.0
_QSO_HISTORY_LOCK = threading.Lock()


def _retain_qso_history(items):
    now = time.time()
    with _QSO_HISTORY_LOCK:
        for it in items or []:
            try:
                mode = str(it.get('mode') or '')
                ch = str(it.get('ch') or '')
                call = str(it.get('call') or '')
                target = str(it.get('target') or '')
                t = str(it.get('time') or '')
                duration = str(it.get('duration') or '')
                if duration == '通话中':
                    key = (mode, ch, call, target, t, 'live')
                else:
                    key = (mode, ch, call, target, t, 'done')
                    for k in [k for k in list(_QSO_HISTORY) if k[:4] == (mode, ch, call, target) and k[5] == 'live']:
                        _QSO_HISTORY.pop(k, None)
                _QSO_HISTORY[key] = {'item': dict(it), 'seen': now}
            except Exception:
                pass
        cutoff = now - _QSO_HISTORY_TTL
        for k in [k for k, v in _QSO_HISTORY.items() if v.get('seen', 0) < cutoff]:
            _QSO_HISTORY.pop(k, None)
        out = [dict(v['item']) for v in _QSO_HISTORY.values()]
    out.sort(key=lambda x: str(x.get('time') or ''), reverse=True)
    return out[:80]


def _qso_payload():
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', 'qso'], timeout=20)
    items = []
    for raw_line in (res['stdout'] or '').split('\n'):
        line = raw_line.strip()
        if not line or line == '=== QSO ===' or line == '\u65e0\u901a\u8bdd\u8bb0\u5f55':
            continue
        try:
            item = parse_qso_line(line)
            if str(item.get('id') or '') in ('9990003', '999000300'):
                continue
            items.append(item)
        except Exception:
            pass
    return {'ok': True, 'qso': _retain_qso_history(_merge_qso(items))}


@app.route('/api/mc/channels-lite')
def api_mc_channels_lite():
    """Read channels.json directly; no sudo, so QSO grid never drops P25."""
    try:
        plan = read_channel_plan()
        rows = []
        for item in (plan.get('list') or []):
            try:
                row = dict(item)
                row['iq'] = os.path.exists('/run/systemd/system/mmdvm-iq@%s.service' % row.get('n')) or True
                row['host'] = True
                rows.append(row)
            except Exception:
                pass
        return jsonify({'ok': True, 'channels': rows, 'source': 'channels.json'})
    except Exception as e:
        return jsonify({'ok': False, 'channels': [], 'error': str(e)}), 200


_QSO_WAIT_LOCK = threading.Lock()
_QSO_WAITERS = [0]


def _qso_live_read():
    try:
        with open('/run/arch-qso-live.json', encoding='utf-8') as f:
            data = json.load(f)
        rows = data.get('qso') if isinstance(data, dict) else []
        if not isinstance(rows, list):
            rows = []
        ver = str(data.get('updated') or '') if isinstance(data, dict) else ''
        return rows, ver
    except Exception:
        return [], ''


@app.route('/api/qso-live')
def api_qso_live():
    """Live TX state from MMDVM-Host MQTT events.

    [FIX 1.0.132] Bounded long-poll: the page passes ?since=<ver>; when the
    state is unchanged we hold the request and return the instant
    /run/arch-qso-live.json changes, so the red live cell lights up / clears
    in well under 0.1 s.  At most 2 requests may wait at a time (gunicorn has
    only 4 threads) -- beyond that we answer immediately so the pool never
    starves.  Detection is a 40 ms stat of a ~0.5 kB file: negligible CPU.
    """
    rows, ver = _qso_live_read()
    since = request.args.get('since') or ''
    waited = False
    if since and since == ver:
        with _QSO_WAIT_LOCK:
            _QSO_WAITERS[0] += 1
            can_wait = _QSO_WAITERS[0] <= 2
        if can_wait:
            waited = True
            deadline = time.time() + 10.0
            try:
                # [FIX 1.0.133] cheap stat per tick instead of read+json parse;
                # only parse when the file actually changed (keeps CPU ~0).
                try:
                    _mt = os.path.getmtime('/run/arch-qso-live.json')
                except Exception:
                    _mt = None
                while time.time() < deadline:
                    time.sleep(0.05)
                    try:
                        if os.path.getmtime('/run/arch-qso-live.json') == _mt:
                            continue
                    except Exception:
                        continue
                    rows, ver = _qso_live_read()
                    break
            finally:
                with _QSO_WAIT_LOCK:
                    _QSO_WAITERS[0] -= 1
        else:
            with _QSO_WAIT_LOCK:
                _QSO_WAITERS[0] -= 1
    return jsonify({'ok': True, 'qso': rows, 'source': 'mqtt-events',
                    'ver': ver, 'waited': waited})


def _qso_refresh_once():
    # [FIX 1.0.90] 后台刷新：把 sudo web-manage.sh qso 从请求路径里挪走。
    # 原来每次 /api/qso 都要起一个进程解析日志（实测 0.27s，开机冷启动更久），
    # 页面在开机后要等它才出列表。
    try:
        payload = _qso_payload()
        with _QSO_LOCK:
            _QSO_CACHE['t'] = time.time()
            _QSO_CACHE['payload'] = payload
    except Exception:
        pass


def _qso_refresher():
    while True:
        _qso_refresh_once()
        time.sleep(5.0)


try:
    import threading as _qso_threading
    _qso_threading.Thread(target=_qso_refresher, daemon=True).start()
except Exception:
    pass


@app.route('/api/qso')
def api_qso():
    """[FIX 1.0.90] 秒回缓存（后台每 3 秒刷新），页面不再等进程。"""
    payload = _QSO_CACHE['payload']
    if payload is None:
        # [FIX 1.0.132] refresh once synchronously so the very first load is not empty
        _qso_refresh_once()
        payload = _QSO_CACHE['payload']
    if payload is None:
        return jsonify({'ok': True, 'qso': []})
    return jsonify(payload)


def get_telemetry(light=False):
    _status_action = "status-quick" if light else "status"
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', _status_action])
    services, profile, system, network, qso = {}, "未知", {}, {}, []
    current_sec = None
    if res['ok']:
        for raw_line in res['stdout'].split('\n'):
            line = raw_line.strip()
            if line == '=== SERVICES ===': current_sec = 'services'; continue
            if line == '=== PROFILE ===': current_sec = 'profile'; continue
            if line == '=== SYSTEM ===': current_sec = 'system'; continue
            if line == '=== NETWORK ===': current_sec = 'network'; continue
            if line == '=== RF FREQ ===': current_sec = 'rf_freq'; continue
            if line == '=== QSO ===': current_sec = 'qso'; continue
            if current_sec == 'services' and line:
                parts = line.split()
                if len(parts) == 2: services[parts[0]] = (parts[1] == 'active')
            elif current_sec == 'profile' and line:
                m = re.search(r'MODE=(\S+)', line)
                if m: profile = m.group(1)
            elif current_sec == 'system' and '=' in line:
                k, v = line.split('=', 1); system[k] = v
            elif current_sec == 'rf_freq' and '=' in line:
                k, v = line.split('=', 1); system[k] = v
            elif current_sec == 'network' and '=' in line:
                k, v = line.split('=', 1); network[k] = v
            elif current_sec == 'qso' and line and line != '无通话记录':
                qso.append(parse_qso_line(line))
    qso = _retain_qso_history(_merge_qso(qso))  # 合并头/尾并保留短通联历史
    # ★ 从 /etc/MMDVM-Host.ini 读取实时频率
    rx_hz = 0; tx_hz = 0
    try:
        with open("/etc/MMDVM-Host.ini", encoding="utf-8", errors="ignore") as f:
            section = None
            for line in f:
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    section = s[1:-1].lower()
                    continue
                if section in ("modem", "info"):
                    if s.lower().startswith("rxfrequency=") and not rx_hz:
                        try: rx_hz = int(s.split("=",1)[1].strip())
                        except: pass
                    elif s.lower().startswith("txfrequency=") and not tx_hz:
                        try: tx_hz = int(s.split("=",1)[1].strip())
                        except: pass
    except Exception:
        pass
    system['rx_hz'] = rx_hz
    system['tx_hz'] = tx_hz
    system['offset_hz'] = (tx_hz - rx_hz) if rx_hz and tx_hz else 0

    # ★ 终极方案：sudo cat 直接读文件（不受权限影响）
    _content = read_host_ini_via_sudo()
    _parsed = parse_host_ini(_content) if _content else {'rx_hz':0,'tx_hz':0,'callsign':'','id':'0'}
    # 合并：status 输出优先，其次 sudo cat 结果
    try:
        _rx = int(system.get('rx_hz') or 0)
    except (ValueError, TypeError):
        _rx = 0
    try:
        _tx = int(system.get('tx_hz') or 0)
    except (ValueError, TypeError):
        _tx = 0
    if not _rx:
        _rx = _parsed['rx_hz']
    if not _tx:
        _tx = _parsed['tx_hz']
    # ★ 通道规划是射频事实来源；Host.ini 只作兼容回退。
    _plan, _primary = plan_primary()
    if _primary:
        try:
            _rx = int(_primary.get('rx') or 0)
            _tx = int(_primary.get('tx') or 0)
        except Exception:
            pass
    system['rx_hz'] = _rx
    system['tx_hz'] = _tx
    system['offset_hz'] = (_tx - _rx) if (_rx and _tx) else 0
    if _plan.get('callsign'):
        system['callsign'] = str(_plan.get('callsign'))
    elif not system.get('callsign'):
        system['callsign'] = _parsed['callsign'] or ''
    if _plan.get('dmr_id') and str(_plan.get('dmr_id')) not in ('', '0', '000000000'):
        system['id'] = str(_plan.get('dmr_id'))
    elif not system.get('id') or system.get('id') == '0':
        system['id'] = _parsed['id'] or '0'
    system['radio_id'] = system.get('id') or '0'
    if os.path.exists('/etc/mmdvm/soapy-sx-verified'):
        system['sx_probe'] = 'OK'

    return {'services': services, 'profile': profile,
            'system': system, 'network': network,
            'qso': None if light else qso[::-1]}

HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARCH 业余无线电融合中枢</title>
<style>
:root{
  --bg:#070a0f;
  --sidebar-bg:#0a0e14;
  --sidebar-hover:#121924;
  --card:#0d131b;
  --card-hi:#111823;
  --card-elev:#151d2a;
  --border:#18202c;
  --border-lit:#222b3a;
  --text:#e8eef8;
  --text-2:#c8d2df;
  --muted:#7d8899;
  --dim:#4a5464;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,"Courier New",monospace;

  /* 主题色（默认绿） */
  --theme-1:#10b981;
  --theme-2:#34d399;
  --theme-3:#059669;
  --theme-rgb:16,185,129;
}
/* 每个页面切换主题 */
body[data-theme="dashboard"]{--theme-1:#10b981;--theme-2:#34d399;--theme-3:#059669;--theme-rgb:16,185,129}
body[data-theme="rf"]       {--theme-1:#10b981;--theme-2:#34d399;--theme-3:#059669;--theme-rgb:16,185,129}
body[data-theme="dmr"]      {--theme-1:#10b981;--theme-2:#34d399;--theme-3:#059669;--theme-rgb:16,185,129}
body[data-theme="ysf"]      {--theme-1:#06b6d4;--theme-2:#22d3ee;--theme-3:#0891b2;--theme-rgb:6,182,212}
body[data-theme="p25"]      {--theme-1:#8b5cf6;--theme-2:#a78bfa;--theme-3:#7c3aed;--theme-rgb:139,92,246}
body[data-theme="nxdn"]     {--theme-1:#ec4899;--theme-2:#f472b6;--theme-3:#db2777;--theme-rgb:236,72,153}
body[data-theme="dstar"]    {--theme-1:#f97316;--theme-2:#fb923c;--theme-3:#ea580c;--theme-rgb:249,115,22}
body[data-theme="fm"]       {--theme-1:#10b981;--theme-2:#34d399;--theme-3:#059669;--theme-rgb:16,185,129}
body[data-theme="pocsag"]   {--theme-1:#f59e0b;--theme-2:#fbbf24;--theme-3:#d97706;--theme-rgb:245,158,11}
body[data-theme="ports"]    {--theme-1:#10b981;--theme-2:#34d399;--theme-3:#059669;--theme-rgb:16,185,129}
body[data-theme="db"]       {--theme-1:#10b981;--theme-2:#34d399;--theme-3:#059669;--theme-rgb:16,185,129}

*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{
  background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
  font-size:15px;line-height:1.5;
  display:flex;overflow:hidden;
  -webkit-font-smoothing:antialiased;
  position:relative;
}

/* ==================== 全局背景光晕 ==================== */
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;background:radial-gradient(900px 600px at 12% -8%,rgba(var(--theme-rgb),.10),transparent 62%),radial-gradient(760px 520px at 92% 6%,rgba(56,189,248,.06),transparent 60%)}
.sb-icon{
  width:36px;height:36px;border-radius:50%;flex-shrink:0;
  background:linear-gradient(135deg,var(--theme-2) 0%, var(--theme-1) 50%, var(--theme-3) 100%);
  color:#fff;display:grid;place-items:center;
  box-shadow:0 0 24px rgba(var(--theme-rgb),.5),inset 0 0 12px rgba(255,255,255,.2);
}
.sb-name{font-size:16.5px;font-weight:800;letter-spacing:.2px;color:#fff}
.sb-sub{font-size:13px;color:var(--muted);margin-top:2px}
.sb-brand::after{
  content:'';position:absolute;left:14px;right:14px;bottom:-1px;height:1px;
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.4),transparent);
}
.sb-brand{
  padding:14px 14px 16px;
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:10px;
  position:relative;
}

.sb-nav{padding:12px 8px;display:flex;flex-direction:column;gap:2px}
/* .sb-nav-label 已移除（用户要求删掉「」字样）*/
.sb-item{
  display:flex;align-items:center;gap:10px;
  padding:10px 12px;border-radius:8px;
  color:#c8d2df;font-size:16px;font-weight:600;
  cursor:pointer;transition:all .18s;
  user-select:none;position:relative;
}
.sb-item:hover{
  background:linear-gradient(90deg,rgba(var(--theme-rgb),.08),var(--sidebar-hover));
  color:#fff;
}
.sb-item.active{
  background:linear-gradient(135deg,var(--theme-2) 0%, var(--theme-1) 50%, var(--theme-3) 100%);
  color:#fff;font-weight:700;
  box-shadow:0 6px 20px rgba(var(--theme-rgb),.4), inset 0 1px 0 rgba(255,255,255,.15);
}
.sb-item .ico{width:16px;height:16px;flex-shrink:0;display:grid;place-items:center;font-size:15px;opacity:.85}
.sb-item.active .ico{opacity:1;filter:drop-shadow(0 0 4px rgba(255,255,255,.5))}

/* ==================== 主区 ==================== */
.main{
  flex:1;height:100vh;overflow-y:auto;
  position:relative;display:flex;flex-direction:column;
  z-index:1;
}
.main-top{
  position:sticky;top:0;z-index:20;
  background:rgba(7,10,15,.85);
  backdrop-filter:blur(18px);
  border-bottom:1px solid var(--border);
  padding:9px 20px;
  display:flex;align-items:center;justify-content:flex-end;
  min-height:42px;
}
.main-top::after{
  content:'';position:absolute;left:0;right:0;bottom:-1px;height:1px;
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.3),transparent);
}
.status-pill{
  display:inline-flex;align-items:center;gap:6px;
  padding:4px 12px;border-radius:20px;
  background:linear-gradient(135deg,rgba(var(--theme-rgb),.15),rgba(var(--theme-rgb),.05));
  color:var(--theme-2);
  border:1px solid rgba(var(--theme-rgb),.4);
  font-size:13px;font-weight:800;letter-spacing:.4px;
  box-shadow:0 0 16px rgba(var(--theme-rgb),.15);
}
.status-pill .dot{
  width:6px;height:6px;border-radius:50%;
  background:linear-gradient(135deg,var(--theme-2),var(--theme-1));
  box-shadow:0 0 8px var(--theme-2);
  animation:pls 2s ease-in-out infinite;
}
@keyframes pls{0%,100%{opacity:1}50%{opacity:.45}}

.content{flex:1;padding:14px 18px 32px;width:100%;position:relative;z-index:1}

/* ==================== 卡片（带主题渐变边框） ==================== */
.card{
  background:linear-gradient(180deg,var(--card-hi) 0%,var(--card) 100%);
  border:1px solid var(--border);
  border-radius:11px;
  padding:16px 18px;
  margin-bottom:12px;
  position:relative;overflow:hidden;
}
.card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.5),transparent);
}
.card::after{
  content:'';position:absolute;top:0;left:0;width:3px;height:100%;
  background:linear-gradient(180deg,transparent,rgba(var(--theme-rgb),.35) 50%,transparent);
  opacity:.6;
}
.card-head{
  display:flex;align-items:flex-start;justify-content:space-between;
  gap:12px;margin-bottom:12px;
}
.card-head-left{display:flex;align-items:flex-start;gap:10px}
.card-head-left .h-ico{
  width:30px;height:30px;border-radius:8px;
  display:grid;place-items:center;flex-shrink:0;
  background:linear-gradient(135deg,rgba(var(--theme-rgb),.2),rgba(var(--theme-rgb),.08));
  color:var(--theme-2);font-size:16px;
  border:1px solid rgba(var(--theme-rgb),.3);
  box-shadow:0 0 12px rgba(var(--theme-rgb),.15);
}
.card-title{font-size:17px;font-weight:800;color:#fff;letter-spacing:.15px}
.card-sub{font-size:13px;color:var(--muted);margin-top:2px;line-height:1.5}
.card-badge{
  padding:4px 11px;border-radius:16px;
  font-size:11.5px;font-weight:800;letter-spacing:.4px;
  background:linear-gradient(135deg,rgba(var(--theme-rgb),.18),rgba(var(--theme-rgb),.06));
  color:var(--theme-2);
  border:1px solid rgba(var(--theme-rgb),.35);
  box-shadow:0 0 12px rgba(var(--theme-rgb),.1);
}

/* ==================== 模式切换 ==================== */
.mode-strip{display:grid;grid-template-columns:repeat(7,1fr);gap:7px;margin-top:4px}
.mode-pill{
  padding:11px 6px;border-radius:8px;
  border:1px solid var(--border-lit);
  background:linear-gradient(180deg,rgba(255,255,255,.02),rgba(255,255,255,.005));
  color:#9aa6b6;cursor:pointer;transition:all .16s;
  display:flex;flex-direction:column;align-items:center;gap:3px;
  font-weight:700;font-size:14px;position:relative;overflow:hidden;
}
.mode-pill:hover{
  border-color:rgba(var(--theme-rgb),.5);color:#fff;
  background:linear-gradient(180deg,rgba(var(--theme-rgb),.1),rgba(var(--theme-rgb),.03));
}
.mode-pill.active{
  background:linear-gradient(180deg,var(--theme-2) 0%,var(--theme-1) 50%,var(--theme-3) 100%);
  border-color:var(--theme-2);color:#fff;
  box-shadow:0 6px 20px rgba(var(--theme-rgb),.45),inset 0 1px 0 rgba(255,255,255,.2);
}
.mode-pill .st{font-size:11.5px;opacity:.85;font-weight:600}
/* ==================== 多信道矩阵 ==================== */
/* 频率模式列表：一行 3 宫格，两行排满 */
.mc-matrix{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:12px}
@media(max-width:860px){.mc-matrix{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:560px){.mc-matrix{grid-template-columns:1fr}}
.mc-card .mc-icon{width:40px;height:40px;flex:0 0 40px;display:block}
.mc-mode-txt{font-size:14.5px;font-weight:800;color:#fff;white-space:nowrap}
.mc-off{font-size:14.5px;font-weight:800;color:#7d93b2;white-space:nowrap}
.mc-card.off{opacity:.62}
.mc-card{border:1px solid var(--border-lit);border-radius:10px;padding:13px;
  background:linear-gradient(180deg,rgba(var(--theme-rgb),.06),rgba(0,0,0,0))}
.mc-card.on{border-color:rgba(16,185,129,.5)}
.mc-hd{display:flex;align-items:center;justify-content:space-between;font-size:15px;font-weight:800;color:#fff;margin-bottom:7px;letter-spacing:.3px}
.mc-dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:middle}
.mc-row{display:flex;gap:6px;margin-top:6px}
.mc-row select{flex:1;min-width:0}
.mc-row .btn{padding:7px 12px;font-size:15.5px;font-weight:700;color:#fff;white-space:nowrap}
.mc-freq{font-family:var(--mono);font-size:15px;font-weight:700;color:#fff;margin-top:7px;line-height:1.55;letter-spacing:.2px}
.mc-empty{padding:16px;text-align:center;color:var(--dim);font-size:13px}
.mc-foot{display:flex;align-items:center;gap:9px;margin-top:12px;flex-wrap:wrap}
.mc-tip{font-size:13.5px;color:var(--dim)}
.bm-bar{margin:0 0 12px;padding:9px 12px;border-radius:8px;font-size:13px;line-height:1.6;
  border:1px solid var(--border-lit);background:#0b1420}
.bm-bar.ok{border-color:rgba(16,185,129,.55);color:#a7f3d0}
.bm-bar.bad{border-color:rgba(239,68,68,.55);color:#fecaca}
.bm-bar b{font-weight:700}
/* ==================== Live QSO 多信道布局（每通道一行 × 4 宫格） ==================== */
.qso-list{display:block !important;grid-template-columns:none !important}
.qso-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.qso-cell{display:flex;flex-direction:column;gap:6px}
.qso-cell .qso-item,.qso-cell .qso-cell-empty{height:100%;min-height:104px}
.qso-prev{font-size:13.5px;color:#9fb3cc}
.qso-prev b{color:#e8eef8;font-family:var(--mono)}
.qso-slot-lbl{display:flex;align-items:center;gap:7px}
.qso-slot-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;background:#64748b}
.qso-slot-dot.live{background:#f87171;box-shadow:0 0 8px rgba(239,68,68,.85);animation:none}
.qso-slot-dot.idle{background:#34d399;box-shadow:0 0 6px rgba(52,211,153,.5)}
.qso-slot-state{font-size:12px;font-weight:700;border-radius:4px;padding:1px 7px;margin-left:auto;white-space:nowrap}
.qso-slot-state.live{background:rgba(239,68,68,.16);color:#fca5a5;border:1px solid rgba(239,68,68,.4)}
.qso-slot-state.idle{background:rgba(52,211,153,.12);color:#6ee7b7;border:1px solid rgba(52,211,153,.3)}
.qso-live-badge{font-size:11px;font-weight:800;color:#fecaca;background:rgba(239,68,68,.22);border:1px solid rgba(239,68,68,.45);border-radius:4px;padding:1px 7px}
.qso-ch{margin-bottom:14px}
.qso-ch:last-child{margin-bottom:0}
.qso-ch-head{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:700;
  color:#cfe2ff;margin:0 0 8px;flex-wrap:wrap}
.qso-ch-dot{width:8px;height:8px;border-radius:50%;
  background:linear-gradient(135deg,var(--theme-2),var(--theme-1));
  box-shadow:0 0 8px var(--theme-2);flex-shrink:0}
.qso-ch-cnt{margin-left:auto;font-size:11.5px;font-weight:600;color:var(--dim)}
.qso-ch-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.qso-cell-empty{min-height:104px;display:flex;align-items:center;justify-content:center;gap:7px;
  border:1px dashed var(--border-lit);border-radius:13px;background:#08101c;
  color:var(--dim);font-size:12px}
.qso-cell-empty .dot{width:6px;height:6px;border-radius:50%;
  background:linear-gradient(135deg,var(--theme-2),var(--theme-1));opacity:.6;
  box-shadow:0 0 8px var(--theme-2);animation:none}
@media (max-width:1200px){ .qso-ch-grid{grid-template-columns:repeat(2,minmax(0,1fr))} }
@media (max-width:600px){ .qso-ch-grid{grid-template-columns:1fr} }

/* ==================== 频率与信道 ==================== */
.freq-now{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.freq-chip{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border-radius:8px;
  border:1px solid var(--border-lit);background:#0b1420;font-size:12.5px;font-family:var(--mono)}
.freq-chip b{color:#6ee7b7}
.freq-rows{display:flex;flex-direction:column;gap:8px;margin-top:14px}
.freq-row{display:grid;grid-template-columns:84px 1fr 1fr 1fr auto;gap:8px;align-items:center;
  padding:9px 11px;border:1px solid var(--border-lit);border-radius:9px;background:#0a121d}
.freq-row .fr-n{font-size:14px;font-weight:700;color:#9fb6d6}
.freq-row select,.freq-row input{min-width:0}
@media (max-width:900px){
  .freq-row{grid-template-columns:1fr 1fr;grid-auto-rows:auto}
  .freq-row .fr-n{grid-column:1/-1}
}
.freq-strip{margin:0 0 12px;padding:8px 12px;border-radius:8px;border:1px dashed var(--border-lit);
  background:#08101c;color:#8fa8c8;font-size:12px;font-family:var(--mono);line-height:1.6}
.freq-strip::before{content:'各通道频率（在 Multi 多信道配置 → 信道规划 里设置）： ';color:#6ee7b7;font-weight:700}

/* ==================== 数据库条 ==================== */
.db-bar{
  display:flex;align-items:center;gap:12px;
  padding:12px 18px;border-radius:11px;margin-bottom:12px;
  background:linear-gradient(90deg,rgba(var(--theme-rgb),.12) 0%,rgba(var(--theme-rgb),.03) 50%,transparent 100%);
  border:1px solid rgba(var(--theme-rgb),.35);
  position:relative;overflow:hidden;
}
.db-bar::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.6),transparent);
}
.db-bar-ico{
  width:34px;height:34px;border-radius:9px;flex-shrink:0;
  background:linear-gradient(135deg,rgba(var(--theme-rgb),.25),rgba(var(--theme-rgb),.08));
  color:var(--theme-2);display:grid;place-items:center;
  border:1px solid rgba(var(--theme-rgb),.4);
  box-shadow:0 0 14px rgba(var(--theme-rgb),.2);
}
.db-bar-txt{flex:1}
.db-bar-txt b{display:block;font-size:14.5px;font-weight:800;color:#fff}
.db-bar-txt span{display:block;font-size:13px;color:var(--muted);margin-top:2px}
.db-bar-tag{
  display:inline-block;margin-left:7px;padding:2px 7px;
  font-size:11.5px;font-weight:800;color:var(--theme-2);
  background:linear-gradient(135deg,rgba(var(--theme-rgb),.2),rgba(var(--theme-rgb),.05));
  border-radius:4px;border:1px solid rgba(var(--theme-rgb),.4);
}

/* ==================== 按钮 ==================== */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
  padding:8px 16px;border-radius:8px;
  font-weight:700;font-size:13px;
  transition:all .16s;border:1px solid transparent;cursor:pointer;
}
.btn-green{
  background:linear-gradient(135deg,var(--theme-2) 0%,var(--theme-1) 50%,var(--theme-3) 100%);
  color:#fff;border-color:var(--theme-1);
  box-shadow:0 3px 14px rgba(var(--theme-rgb),.4);
}
.btn-green:hover{box-shadow:0 5px 22px rgba(var(--theme-rgb),.55);transform:translateY(-1px)}
.btn-blue{
  background:linear-gradient(135deg,#60a5fa 0%,#3b82f6 50%,#2563eb 100%);
  color:#fff;border-color:#3b82f6;
  box-shadow:0 3px 14px rgba(59,130,246,.35);
}
.btn-blue:hover{box-shadow:0 5px 22px rgba(59,130,246,.5);transform:translateY(-1px)}
.btn-ghost{
  background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.01));
  color:#c8d2df;border-color:var(--border-lit);
}
.btn-ghost:hover{background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.03));color:#fff}
.btn-sm{padding:6px 11px;font-size:13px;border-radius:7px}

/* ==================== 统计卡 ==================== */
.stat-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:11px;margin-bottom:12px}
.stat{
  background:linear-gradient(180deg,var(--card-hi),var(--card));
  border:1px solid var(--border);
  border-radius:10px;padding:13px 15px;
  display:flex;align-items:center;gap:12px;
  position:relative;overflow:hidden;
}
.stat::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.35),transparent);
}
.stat-ico{
  width:38px;height:38px;border-radius:10px;flex-shrink:0;
  display:grid;place-items:center;font-size:18px;
}
.stat-ico.r{background:linear-gradient(135deg,rgba(239,68,68,.2),rgba(239,68,68,.06));color:#f87171;border:1px solid rgba(239,68,68,.25)}
.stat-ico.b{background:linear-gradient(135deg,rgba(59,130,246,.2),rgba(59,130,246,.06));color:#60a5fa;border:1px solid rgba(59,130,246,.25)}
.stat-ico.a{background:linear-gradient(135deg,rgba(245,158,11,.2),rgba(245,158,11,.06));color:#fbbf24;border:1px solid rgba(245,158,11,.25)}
.stat-ico.g{background:linear-gradient(135deg,rgba(var(--theme-rgb),.22),rgba(var(--theme-rgb),.06));color:var(--theme-2);border:1px solid rgba(var(--theme-rgb),.3)}
.stat-ico.y{background:linear-gradient(135deg,rgba(34,211,238,.2),rgba(34,211,238,.06));color:#22d3ee;border:1px solid rgba(34,211,238,.25)}
/* ==================== 通话记录表格（紧凑）==================== */
.qso-table{width:100%;border-collapse:collapse;font-size:13.5px}
.qso-table th{position:sticky;top:0;z-index:1;background:#111c2e;color:var(--dim);font-weight:800;font-size:12.5px;letter-spacing:.4px;text-align:left;padding:5px 7px;border-bottom:1px solid var(--border);white-space:nowrap}
.qso-table td{padding:3px 7px;border-bottom:1px solid rgba(255,255,255,.05);white-space:nowrap;vertical-align:middle}
.qso-table tbody tr:nth-child(even) td{background:rgba(255,255,255,.018)}
.qso-table tbody tr:hover td{background:rgba(96,165,250,.08)}
.qso-table tbody tr.new td{background:rgba(52,211,153,.09)}
.qso-table td.mono{font-family:Consolas,monospace;color:#9fb3cc}
.qso-wrap{overflow-x:auto;border-top:1px solid var(--border)}
.qso-slot{font-size:11.5px;font-weight:800;color:#fbbf24;background:rgba(245,158,11,.14);border-radius:3px;padding:0 3px;margin-left:4px}
.stat-lbl{font-size:13.5px;color:var(--muted);font-weight:800;letter-spacing:.5px;text-transform:uppercase}
.stat-val{font-size:19px;font-weight:800;color:#fff;margin-top:3px;font-family:var(--mono);letter-spacing:-.2px}

/* ==================== QSO 卡片（方格平铺，每条不同渐变色） ==================== */
.qso-list{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:10px;
  max-width:100%;
}
@media (max-width:1200px){
  .qso-list{grid-template-columns:repeat(3,minmax(0,1fr))}
}
@media (max-width:900px){
  .qso-list{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (max-width:600px){
  .qso-list{grid-template-columns:1fr}
}
.qso-item{
  --qso-c1:#10b981;
  --qso-c2:#34d399;
  --qso-rgb:16,185,129;
  background:linear-gradient(135deg,
    rgba(var(--qso-rgb),.06) 0%,
    rgba(var(--qso-rgb),.02) 40%,
    var(--card) 100%);
  border:1px solid rgba(var(--qso-rgb),.20);
  border-radius:11px;
  padding:12px 14px 11px 16px;
  transition:all .2s;
  position:relative;overflow:hidden;
  min-height:96px;
  display:flex;flex-direction:column;
}
/* 左侧渐变竖条 */
.qso-item::before{
  content:'';position:absolute;left:0;top:0;bottom:0;width:3px;
  background:linear-gradient(180deg,
    var(--qso-c2) 0%,
    var(--qso-c1) 50%,
    var(--qso-c2) 100%);
  box-shadow:0 0 6px rgba(var(--qso-rgb),.35);
}
/* 顶部渐变高光 */
.qso-item::after{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,
    transparent 0%,
    var(--qso-c2) 50%,
    transparent 100%);
  opacity:.4;
}
/* 对角光晕 */
.qso-item .glow{
  position:absolute;top:-30%;right:-20%;width:60%;height:80%;
  background:radial-gradient(circle,rgba(var(--qso-rgb),.05) 0%,transparent 70%);
  pointer-events:none;
}
.qso-item:hover{
  border-color:rgba(var(--qso-rgb),.45);
  background:linear-gradient(135deg,
    rgba(var(--qso-rgb),.12) 0%,
    rgba(var(--qso-rgb),.04) 40%,
    var(--card-hi) 100%);
  box-shadow:
    0 4px 14px rgba(var(--qso-rgb),.12),
    inset 0 0 12px rgba(var(--qso-rgb),.03);
  transform:translateY(-1px);
}
.qso-row1{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.qso-country{
  display:inline-flex;align-items:center;gap:5px;
  padding:2px 8px;border-radius:4px;
  font-size:12px;font-weight:700;
  background:linear-gradient(135deg,rgba(255,255,255,.10),rgba(255,255,255,.03));
  color:#e8eef8;
  border:1px solid rgba(255,255,255,.15);
  white-space:nowrap;
  letter-spacing:.2px;
}
.qso-flag-img{
  width:18px;height:13px;border-radius:2px;
  object-fit:cover;flex-shrink:0;
  box-shadow:0 0 4px rgba(0,0,0,.4);
  border:1px solid rgba(255,255,255,.15);
  vertical-align:middle;
}
.qso-flag-emoji{
  font-size:13px;line-height:1;
}
.qso-country-name{
  font-family:-apple-system,BlinkMacSystemFont,"Microsoft YaHei",sans-serif;
}
.qso-id{
  display:inline-block;padding:1px 6px;border-radius:4px;
  font-size:12.5px;font-weight:700;font-family:var(--mono);
  background:linear-gradient(135deg,rgba(59,130,246,.25),rgba(59,130,246,.08));
  color:#93c5fd;
  border:1px solid rgba(59,130,246,.4);
  letter-spacing:.2px;
}
.qso-call{
  font-size:17px;font-weight:800;
  letter-spacing:.5px;font-family:var(--mono);
  background:linear-gradient(135deg,var(--qso-c2) 0%,var(--qso-c1) 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  background-clip:text;
  filter:drop-shadow(0 0 4px rgba(var(--qso-rgb),.25));
}
.qso-tag{
  display:inline-block;padding:2px 9px;
  font-size:11.5px;font-weight:800;letter-spacing:.5px;
  border-radius:4px;font-family:var(--mono);
}
.qso-tag.mode{
  background:linear-gradient(135deg,rgba(var(--qso-rgb),.3),rgba(var(--qso-rgb),.1));
  color:var(--qso-c2);
  border:1px solid rgba(var(--qso-rgb),.5);
  box-shadow:0 0 8px rgba(var(--qso-rgb),.25);
}
.qso-tag.src-rf{
  background:linear-gradient(135deg,rgba(59,130,246,.25),rgba(59,130,246,.08));
  color:#60a5fa;border:1px solid rgba(59,130,246,.45);
}
.qso-tag.src-net{
  background:linear-gradient(135deg,rgba(16,185,129,.25),rgba(16,185,129,.08));
  color:#34d399;border:1px solid rgba(16,185,129,.45);
}
.qso-tag.src-local{
  background:linear-gradient(135deg,rgba(139,92,246,.25),rgba(139,92,246,.08));
  color:#a78bfa;border:1px solid rgba(139,92,246,.45);
}
.qso-time{
  margin-left:auto;font-family:var(--mono);font-size:11.5px;
  color:var(--muted);
  background:rgba(var(--qso-rgb),.1);
  padding:2px 7px;border-radius:4px;
  border:1px solid rgba(var(--qso-rgb),.2);
}
.qso-row2{
  display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  font-size:13px;color:var(--muted);
  padding-top:8px;
  margin-top:auto;
  border-top:1px solid rgba(var(--qso-rgb),.15);
}
.qso-row2 b{
  color:#e8eef8;font-family:var(--mono);font-weight:700;
  text-shadow:0 0 10px rgba(var(--qso-rgb),.35);
}
.ber{padding:2px 7px;border-radius:4px;font-family:var(--mono);font-weight:800;font-size:12px}
.ber.good{background:linear-gradient(135deg,rgba(16,185,129,.25),rgba(16,185,129,.08));color:#34d399;border:1px solid rgba(16,185,129,.45)}
.ber.warn{background:linear-gradient(135deg,rgba(245,158,11,.25),rgba(245,158,11,.08));color:#fbbf24;border:1px solid rgba(245,158,11,.45)}
.ber.bad{background:linear-gradient(135deg,rgba(239,68,68,.25),rgba(239,68,68,.08));color:#f87171;border:1px solid rgba(239,68,68,.45)}
/* ==================== ★ Live QSO 看板：整体放大一号 ==================== */
#qsoPanel .card-title{font-size:19px}
#qsoPanel .card-badge{font-size:14px}
#qsoPanel .qso-ch-head{font-size:15.5px}
/* 时隙标签（时隙 1 (TS1) / 时隙 2 (TS2)）：大 0.5 号 + 红色 */
#qsoPanel .qso-slot-lbl{font-size:13px;font-weight:800;color:#cfe2ff;letter-spacing:.4px;margin:3px 0 5px}
#qsoPanel .qso-ch-cnt{font-size:13px}
#qsoPanel .qso-call{font-size:19px}
#qsoPanel .qso-tag{font-size:13px;padding:2px 10px}
#qsoPanel .qso-time{font-size:14px;padding:2px 8px}
#qsoPanel .qso-id{font-size:13px}
#qsoPanel .qso-country{font-size:13.5px;padding:2px 9px}
#qsoPanel .qso-flag-emoji{font-size:15px}
#qsoPanel .qso-flag-img{width:20px;height:15px}
#qsoPanel .qso-row2{font-size:14.5px}
#qsoPanel .qso-row2 b{font-size:13.5px}
#qsoPanel .ber{font-size:13.5px;padding:2px 8px}
#qsoPanel .qso-empty{font-size:15.5px}
#qsoPanel .qso-cell-empty{font-size:14.5px}
#qsoPanel .qso-empty .dot{width:7px;height:7px}
#qsoPanel .qso-ch-dot{width:9px;height:9px}
/* ★ 正在工作的「宫格」（格子本身）：淡红底（不动通道外框） */
#qsoPanel .qso-item.qso-live{
  background:linear-gradient(180deg,rgba(239,68,68,.50),rgba(239,68,68,.24)) !important;
  border-color:rgba(239,68,68,.78) !important;
  box-shadow:0 0 0 2px rgba(239,68,68,.48),0 0 22px rgba(239,68,68,.38) !important;
}
#qsoPanel .qso-item.qso-live .qso-time{background:rgba(239,68,68,.22);color:#fecaca;border-color:rgba(239,68,68,.45)}
#qsoPanel .qso-item.qso-live .glow{opacity:1}
/* ==================== 系统设置：系统信息 / 网络状态（白字、大一号）==================== */
#sysInfo,#sysNet{font-size:16px !important;color:#fff !important;line-height:1.9}
#sysInfo b,#sysNet b,#sysInfo span,#sysNet span{color:#fff}
#sysInfo .kvw{display:inline-block;min-width:74px;color:#9fb3cc}
/* 网络状态 / 系统信息 的对齐行样式 */
.netrow{display:flex;align-items:center;gap:9px;padding:3px 0;flex-wrap:wrap}
.netico{width:20px;text-align:center;font-size:15px;opacity:.95;flex-shrink:0}
.netlab{min-width:44px;color:#9fb3cc;font-weight:600;flex-shrink:0}
.netdot{width:8px;height:8px;border-radius:50%;flex-shrink:0;background:#64748b}
.netdot.ok{background:#34d399;box-shadow:0 0 7px rgba(52,211,153,.65)}
.netdot.off{background:#64748b;box-shadow:none}
.netdot-none{background:transparent}
.netval{font-family:var(--mono);font-weight:700;color:#fff;min-width:150px;letter-spacing:.3px}
.netval.off{color:#9fb3cc;font-weight:600}
.netval.mono{min-width:0}
.netmeta{color:#c9d6e6;font-size:14px}
.netmeta-k{color:#9fb3cc;margin-right:4px}
.netsep{margin:0 8px;color:#64748b}
#sysNet .dimtxt{color:#9fb3cc}
.qso-empty{padding:36px 20px;text-align:center;color:var(--dim);font-size:14px;grid-column:1/-1}
.qso-empty .dot{
  display:inline-block;width:6px;height:6px;border-radius:50%;
  background:linear-gradient(135deg,var(--theme-2),var(--theme-1));
  margin-right:6px;box-shadow:0 0 8px var(--theme-2);
  animation:pls 1.8s ease-in-out infinite;
}

/* ==================== 协议页 ==================== */
.proto-page{display:none}
.proto-page.active{display:block}
.freq-banner{
  display:flex;align-items:center;gap:12px;
  padding:12px 16px;border-radius:10px;margin-bottom:12px;
  background:linear-gradient(90deg,rgba(var(--theme-rgb),.08) 0%,var(--card-hi) 60%,var(--card) 100%);
  border:1px solid rgba(var(--theme-rgb),.25);
  position:relative;overflow:hidden;
}
.freq-banner::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.5),transparent);
}
.freq-banner-ico{
  width:32px;height:32px;border-radius:8px;flex-shrink:0;
  background:linear-gradient(135deg,rgba(var(--theme-rgb),.25),rgba(var(--theme-rgb),.08));
  color:var(--theme-2);display:grid;place-items:center;font-size:15px;
  border:1px solid rgba(var(--theme-rgb),.3);
}
.freq-banner-txt{flex:1}
.freq-banner-txt > b{
  display:block;font-size:14px;color:#fff;font-weight:800;
  margin-bottom:4px;letter-spacing:.15px;
}
.freq-banner-txt > span{
  display:block;font-size:13.5px;color:var(--muted);
  font-family:var(--mono);line-height:1.75;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.freq-banner-txt > span > b{
  display:inline;
  color:#fff;font-weight:800;
  padding:0 2px;
  font-family:var(--mono);
}

.preset-box{
  background:linear-gradient(135deg,rgba(var(--theme-rgb),.06),rgba(var(--theme-rgb),.01));
  border:1px solid rgba(var(--theme-rgb),.3);
  border-radius:10px;padding:12px 16px;margin-bottom:12px;
  position:relative;overflow:hidden;
}
.preset-box::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.5),transparent);
}
.preset-box-lbl{
  display:flex;align-items:center;gap:8px;
  font-size:12.5px;font-weight:800;color:var(--theme-2);
  margin-bottom:8px;letter-spacing:.2px;
}
.preset-box select{
  width:100%;padding:9px 12px;border-radius:8px;
  background:rgba(0,0,0,.4);color:#fff;
  border:1px solid var(--border-lit);
  font-size:14.5px;outline:none;
}
.preset-box select:focus{border-color:var(--theme-1);box-shadow:0 0 0 3px rgba(var(--theme-rgb),.15)}

/* ==================== 表单 ==================== */
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px 16px}
.form-grid .fld{display:flex;flex-direction:column;gap:5px}
.form-grid .fld.full{grid-column:1/-1}
.form-grid label{font-size:13px;color:var(--muted);font-weight:700;letter-spacing:.35px}
.form-grid input,
.form-grid select{
  background:linear-gradient(180deg,rgba(0,0,0,.42),rgba(0,0,0,.32));
  border:1px solid var(--border-lit);
  color:#fff;padding:10px 12px;border-radius:8px;
  font-size:15px;outline:none;transition:all .14s;
  min-height:40px;
}
.form-grid input:focus,
.form-grid select:focus{
  border-color:var(--theme-1);
  box-shadow:0 0 0 3px rgba(var(--theme-rgb),.15), inset 0 0 8px rgba(var(--theme-rgb),.05);
}
.form-grid .hint{font-size:13px;color:var(--dim);margin-top:-1px}

/* ==================== 保存按钮（横向多色渐变） ==================== */
.save-row{margin-top:16px;display:flex;align-items:center;gap:10px}
.save-btn{
  flex:1;padding:12px 20px;border-radius:10px;
  color:#fff;font-weight:800;font-size:13.5px;
  letter-spacing:.3px;cursor:pointer;border:none;
  display:inline-flex;align-items:center;justify-content:center;gap:7px;
  transition:all .2s;position:relative;overflow:hidden;
}
.save-btn::before{
  content:'';position:absolute;inset:0;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.15),transparent);
  transform:translateX(-100%);transition:transform .6s;
}
.save-btn:hover::before{transform:translateX(100%)}
/* 每个按钮都是横向多色渐变，更华丽 */
.save-btn.rf    {background:linear-gradient(90deg,#047857 0%,#10b981 40%,#34d399 70%,#06b6d4 100%);box-shadow:0 5px 20px rgba(16,185,129,.35)}
.save-btn.dmr   {background:linear-gradient(90deg,#047857 0%,#10b981 40%,#34d399 70%,#06b6d4 100%);box-shadow:0 5px 20px rgba(16,185,129,.35)}
.save-btn.ysf   {background:linear-gradient(90deg,#0891b2 0%,#06b6d4 35%,#22d3ee 65%,#10b981 100%);box-shadow:0 5px 20px rgba(6,182,212,.35)}
.save-btn.p25   {background:linear-gradient(90deg,#6d28d9 0%,#8b5cf6 35%,#a78bfa 65%,#06b6d4 100%);box-shadow:0 5px 20px rgba(139,92,246,.35)}
.save-btn.nxdn  {background:linear-gradient(90deg,#be185d 0%,#ec4899 30%,#a78bfa 60%,#10b981 100%);box-shadow:0 5px 20px rgba(236,72,153,.35)}
.save-btn.dstar {background:linear-gradient(90deg,#c2410c 0%,#f97316 35%,#fbbf24 65%,#10b981 100%);box-shadow:0 5px 20px rgba(249,115,22,.35)}
.save-btn.pocsag{background:linear-gradient(90deg,#b45309 0%,#f59e0b 35%,#fbbf24 60%,#34d399 100%);box-shadow:0 5px 20px rgba(245,158,11,.35)}
.save-btn:hover{transform:translateY(-1px);filter:brightness(1.1)}

.refresh-btn{
  padding:12px 18px;border-radius:10px;
  background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.02));
  border:1px solid var(--border-lit);
  color:#c8d2df;font-weight:700;font-size:12.5px;
  display:inline-flex;align-items:center;gap:5px;
  cursor:pointer;transition:all .14s;
}
.refresh-btn:hover{
  background:linear-gradient(180deg,rgba(var(--theme-rgb),.12),rgba(var(--theme-rgb),.04));
  border-color:rgba(var(--theme-rgb),.5);color:#fff;
}

/* ==================== 端口卡 ==================== */
.port-card{
  background:linear-gradient(135deg,var(--card-hi),var(--card));
  border:1px solid var(--border-lit);
  border-radius:9px;padding:12px 16px;
  display:flex;align-items:center;gap:14px;
  margin-bottom:8px;transition:all .18s;
  position:relative;overflow:hidden;
}
.port-card::before{
  content:'';position:absolute;left:0;top:0;bottom:0;width:2px;
  background:linear-gradient(180deg,transparent,rgba(var(--theme-rgb),.6) 50%,transparent);
  opacity:0;transition:opacity .2s;
}
.port-card:hover{border-color:rgba(var(--theme-rgb),.4);box-shadow:0 4px 14px rgba(var(--theme-rgb),.08)}
.port-card:hover::before{opacity:1}
.port-num{
  font-family:var(--mono);font-size:14px;font-weight:800;
  color:var(--theme-2);flex-shrink:0;min-width:60px;
  background:linear-gradient(135deg,var(--theme-2),var(--theme-1));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  background-clip:text;
}
.port-info{flex:1}
.port-info b{display:block;font-size:13px;color:#fff}
.port-info span{display:block;font-size:13px;color:var(--muted);margin-top:2px}
.port-badge{
  padding:4px 11px;border-radius:14px;
  font-size:12px;font-weight:800;letter-spacing:.4px;
  font-family:var(--mono);
}
.port-badge.listen{
  background:linear-gradient(135deg,rgba(16,185,129,.22),rgba(16,185,129,.08));
  color:#34d399;border:1px solid rgba(16,185,129,.4);
  box-shadow:0 0 10px rgba(16,185,129,.2);
}
.port-badge.closed{
  background:linear-gradient(135deg,rgba(90,101,119,.12),rgba(90,101,119,.04));
  color:#6b7280;border:1px solid rgba(90,101,119,.25);
}

/* ==================== 数据库页 ==================== */
.db-cards{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.db-card{
  background:linear-gradient(135deg,var(--card-hi),var(--card));
  border:1px solid var(--border-lit);
  border-radius:10px;padding:16px 18px;
  position:relative;overflow:hidden;transition:all .18s;
}
.db-card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.4),transparent);
}
.db-card:hover{border-color:rgba(var(--theme-rgb),.4);box-shadow:0 6px 20px rgba(var(--theme-rgb),.1)}
.db-card h3{font-size:14.5px;font-weight:800;color:#fff;margin-bottom:5px;display:flex;align-items:center;gap:8px}
.db-card p{font-size:13px;color:var(--muted);margin-bottom:14px;line-height:1.55}
.db-card .hdr-tag{
  margin-left:auto;padding:2px 8px;border-radius:4px;
  font-size:11.5px;font-weight:800;letter-spacing:.4px;
}
.db-card .hdr-tag.blue{
  background:linear-gradient(135deg,rgba(59,130,246,.22),rgba(59,130,246,.06));
  color:#60a5fa;border:1px solid rgba(59,130,246,.35);
}
.db-card .hdr-tag.green{
  background:linear-gradient(135deg,rgba(16,185,129,.22),rgba(16,185,129,.06));
  color:#34d399;border:1px solid rgba(16,185,129,.35);
}

.console{
  background:linear-gradient(180deg,#020609,#010407);
  border:1px solid var(--border);
  border-radius:9px;padding:14px 18px;
  font-family:var(--mono);font-size:13.5px;color:#a5d9b9;
  white-space:pre;                    /* ★ 关键：不自动换行 */
  overflow:auto;
  min-height:120px;max-height:520px;
  line-height:1.75;                    /* ★ 关键：加大行高 */
  margin-top:12px;
  box-shadow:inset 0 0 20px rgba(var(--theme-rgb),.05);
  word-break:normal;
  overflow-wrap:normal;
}
.console > div{                      /* 每行独立 div */
  padding:1px 0;
  line-height:1.75;
  white-space:pre;
}
.console .line-odd{opacity:.95}
.console .line-even{opacity:.85}
.console .tag-in{color:var(--theme-2);font-weight:700}

/* ==================== 页脚 ==================== */
.footer{
  margin-top:18px;padding:18px 0 6px;
  border-top:1px solid var(--border);
  text-align:center;color:var(--muted);
  font-size:13px;line-height:1.8;
  position:relative;
}
.footer::before{
  content:'';position:absolute;top:-1px;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.3),transparent);
}
.footer .title{
  color:#c8d2df;font-weight:700;font-size:12.5px;
  display:inline-block;
}
.footer .title-link{
  color:inherit;text-decoration:none;display:inline-block;
  background:linear-gradient(90deg,var(--theme-2),var(--theme-1));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  background-clip:text;border-bottom:1px dashed rgba(var(--theme-rgb),.45);
  transition:all .18s;
}
.footer .title-link:hover{
  border-bottom-color:var(--theme-2);
  filter:drop-shadow(0 0 8px rgba(var(--theme-rgb),.5));
}
.footer .author-link{
  color:var(--theme-2);
  font-weight:700;
  text-decoration:none;
  background:linear-gradient(90deg,var(--theme-2),var(--theme-1));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  background-clip:text;
  border-bottom:1px dashed rgba(var(--theme-rgb),.5);
  transition:all .18s;
}
.footer .author-link:hover{
  border-bottom-color:var(--theme-2);
  filter:drop-shadow(0 0 8px rgba(var(--theme-rgb),.5));
}
.footer .author{
  background:linear-gradient(90deg,var(--theme-2),var(--theme-1));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  background-clip:text;font-weight:700;
}

/* ==================== 移动端 ==================== */
@media (max-width:900px){
  .mode-strip{grid-template-columns:repeat(3,1fr)}
  .stat-grid{grid-template-columns:repeat(2,1fr)}
  .form-grid{grid-template-columns:1fr}
  .db-cards{grid-template-columns:1fr}
}
#mobileSettingsBtn{
  display:none;position:fixed;top:10px;right:10px;z-index:150;
  width:42px;height:42px;border-radius:50%;
  border:1px solid rgba(var(--theme-rgb),.45);
  background:rgba(13,19,27,.94);color:var(--theme-2);
  align-items:center;justify-content:center;font-size:22px;line-height:1;
  box-shadow:0 8px 24px rgba(0,0,0,.35),0 0 16px rgba(var(--theme-rgb),.18);
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  cursor:pointer;padding:0;
}
@media (max-width:768px){
  html,body{height:auto;min-height:100%;width:100%;overflow-x:hidden!important;overflow-y:auto!important;-webkit-overflow-scrolling:touch}
  body{display:block!important;flex-direction:column;overflow:auto!important}
  .sidebar{width:100%;height:auto;position:relative;border-right:none;border-bottom:1px solid var(--border)}
  .sb-nav{display:flex;flex-direction:row;overflow-x:auto;overflow-y:hidden;padding:6px;gap:6px;touch-action:pan-x;-webkit-overflow-scrolling:touch}
  .sb-item{padding:8px 11px;font-size:13px;flex-shrink:0;gap:6px}
  .sb-item .ico{font-size:13px}
  .main{width:100%;min-width:0;height:auto!important;overflow:visible!important;display:block!important}
  .content{width:100%;max-width:100%;padding:12px 10px 80px;overflow-x:hidden}
  .content table{display:block;max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;white-space:nowrap}
  .qso-wrap,.scroll,.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  #mobileSettingsBtn{display:flex!important}
}

/* ============ 下拉框深色主题 ============ */
select,
select.input {
  background: #0a121d !important;
  color: #e8eef8 !important;
  border-color: #243145 !important;
}
select option,
select.input option {
  background: #0d1520 !important;
  color: #e8eef8 !important;
  padding: 8px 12px !important;
}
select option:checked,
select option:hover {
  background: linear-gradient(135deg, #10b981, #047857) !important;
  color: #fff !important;
}
/* 表单里的 select 也统一 */
.form-grid select,
.fld select {
  background: #0a121d !important;
  color: #e8eef8 !important;
}
.form-grid select option,
.fld select option {
  background: #0d1520 !important;
  color: #e8eef8 !important;
}
/* BM 下拉框特化 */
#dmrBmHost,
#dmrBmHost option {
  background: #0a121d !important;
  color: #e8eef8 !important;
}
#dmrBmHost option {
  padding: 10px 14px !important;
  line-height: 1.6 !important;
}
#dmrBmHost option:checked {
  background: linear-gradient(135deg, #10b981, #047857) !important;
  color: #fff !important;
}

/* ==========================================================================
   ★★★ UI v2 科技感皮肤（覆盖层，不改动原有规则结构）
   ========================================================================== */

body{
  background:
    radial-gradient(1200px 600px at 12% -10%, rgba(16,185,129,.10), transparent 60%),
    radial-gradient(900px 500px at 88% 0%, rgba(56,189,248,.10), transparent 55%),
    radial-gradient(700px 700px at 50% 120%, rgba(168,85,247,.07), transparent 60%),
    var(--bg);
  background-attachment:fixed !important;
}
body::before{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:
    linear-gradient(rgba(120,160,220,.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(120,160,220,.045) 1px, transparent 1px);
  background-size:44px 44px, 44px 44px;
  mask-image:radial-gradient(circle at 50% 0%, #000 0%, transparent 78%);
  -webkit-mask-image:radial-gradient(circle at 50% 0%, #000 0%, transparent 78%);
}
body::after{
  content:'';position:fixed;left:0;right:0;top:0;height:2px;z-index:60;pointer-events:none;
  background:linear-gradient(90deg,transparent,#22d3ee,#34d399,#a78bfa,transparent);
  opacity:.55;animation:scanX 7s linear infinite;
}
.card-badge{
  background:rgba(56,189,248,.10) !important;border:1px solid rgba(56,189,248,.32) !important;
  color:#7dd3fc !important;letter-spacing:.6px;
}
.badge,.db-bar-tag{
  background:linear-gradient(135deg,rgba(16,185,129,.16),rgba(56,189,248,.10)) !important;
  border:1px solid rgba(52,211,153,.30) !important;color:#6ee7b7 !important;
}

/* 实时通联看板：一行 4 张，更宽更耐看 */
.qso-list{grid-template-columns:repeat(4,minmax(0,1fr)) !important;gap:12px}
.qso-item{min-height:104px;border-radius:13px;padding:13px 15px 12px 17px}
.qso-item .qso-call{font-size:16px;letter-spacing:.3px}
.qso-item .qso-row2{gap:10px;flex-wrap:wrap}

/* ---------- 智能选择器（服务器 / 反射器） ---------- */
.sp{
  border:1px solid rgba(56,189,248,.22);border-radius:13px;overflow:hidden;
  background:linear-gradient(180deg,rgba(10,18,32,.72),rgba(8,14,26,.86));
}
.sp-head{
  display:flex;align-items:center;gap:10px;padding:10px 13px;
  background:linear-gradient(90deg,rgba(56,189,248,.10),rgba(16,185,129,.05),transparent);
  border-bottom:1px solid rgba(120,160,220,.14);
}
.sp-title{font-size:14px;font-weight:800;letter-spacing:.7px;text-transform:uppercase;color:#bcd2ef}
.sp-hint{font-size:12.5px;color:#6f86a6;margin-left:auto;white-space:nowrap}
.sp-bar{display:flex;align-items:center;gap:9px;padding:10px 13px 4px}
.sp-ico{font-size:14px;opacity:.7}
.sp-in{
  flex:1;background:rgba(3,8,16,.75);border:1px solid rgba(120,160,220,.26);
  color:#e8f1ff;border-radius:9px;padding:9px 12px;font-size:14.5px;font-family:inherit;
  transition:border-color .18s, box-shadow .18s;
}
.sp-in:focus{outline:none;border-color:rgba(56,189,248,.72);box-shadow:0 0 0 3px rgba(56,189,248,.13)}
.sp-in::placeholder{color:#5b7093}
.sp-cnt{font-size:13px;color:#7dd3fc;font-variant-numeric:tabular-nums;white-space:nowrap}
.sp-chips{display:flex;flex-wrap:wrap;gap:6px;padding:9px 13px 6px}
.sp-chip{
  font-size:12.5px;font-weight:700;padding:3.5px 9px;border-radius:99px;cursor:pointer;
  background:rgba(120,160,220,.09);border:1px solid rgba(120,160,220,.20);color:#9fb6d6;
  transition:all .16s;user-select:none;
}
.sp-chip:hover{background:rgba(56,189,248,.16);color:#cfeaff;border-color:rgba(56,189,248,.42)}
.sp-chip.on{
  background:linear-gradient(135deg,rgba(16,185,129,.26),rgba(56,189,248,.20));
  border-color:rgba(52,211,153,.62);color:#a7f3d0;
}
.sp-chip b{opacity:.62;font-weight:700;margin-left:4px}
.sp-list{max-height:236px;overflow:auto;padding:4px 7px 8px}
.sp-list::-webkit-scrollbar{width:9px}
.sp-list::-webkit-scrollbar-thumb{background:rgba(120,160,220,.26);border-radius:9px}
.sp-row{
  display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:9px;cursor:pointer;
  border:1px solid transparent;transition:all .13s;
}
.sp-row:hover{background:rgba(56,189,248,.10);border-color:rgba(56,189,248,.28)}
.sp-row.on{background:linear-gradient(90deg,rgba(16,185,129,.20),rgba(56,189,248,.10));border-color:rgba(52,211,153,.5)}
.sp-row .nm{font-size:14px;font-weight:700;color:#e6efff;min-width:118px}
.sp-row .nt{font-size:11.5px;font-weight:800;padding:2px 6px;border-radius:5px;background:rgba(167,139,250,.16);color:#c4b5fd}
.sp-row .hs{font-size:13px;color:#8fa6c4;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono)}
.sp-row .pt{font-size:12.5px;color:#6f86a6;font-variant-numeric:tabular-nums}
.sp-empty{padding:22px;text-align:center;color:#5b7093;font-size:13.5px}
.sp-sel{
  display:flex;align-items:center;gap:9px;padding:9px 13px;
  border-top:1px solid rgba(120,160,220,.14);
  background:linear-gradient(90deg,rgba(16,185,129,.09),transparent);
  font-size:13.5px;color:#a7f3d0;
}
.sp-sel b{color:#fff;font-family:var(--mono);font-size:13.5px}
.sp-sel .none{color:#fbbf24}
.sp-go{
  margin-left:auto;font-size:12.5px;background:rgba(56,189,248,.14);
  border:1px solid rgba(56,189,248,.36);color:#7dd3fc;border-radius:7px;padding:4px 10px;cursor:pointer;
}
.sp-go:hover{background:rgba(56,189,248,.26)}
.sp-mod{display:flex;gap:10px;align-items:center;padding:10px 13px;border-top:1px solid rgba(120,160,220,.12);flex-wrap:wrap}
.sp-mod label{font-size:13px;color:#9fb6d6}
.sp-mod select{
  background:rgba(3,8,16,.75);border:1px solid rgba(120,160,220,.26);color:#e8f1ff;
  border-radius:8px;padding:6px 9px;font-size:14px;font-family:inherit;
}
.essid-note{font-size:12px;color:#6f86a6;margin-top:5px;line-height:1.55}
.essid-note b{color:#6ee7b7}
/* ★ 恢复：之前被正则误删的规则 */
svg{display:block;flex-shrink:0}
input,select,textarea{font-family:inherit;font-size:inherit}
#toast.ok{background:#0e2f22;border-color:#1d6b4a;color:#a7f3d0}
#toast.bad{background:#331616;border-color:#7f2222;color:#fecaca}
.plrow .fq{font-size:13px}
.plout,pre{font-size:13px}

/* ===== INLINE3_CSS 内联页样式（类名已加前缀，与主控制台隔离）===== */

*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1220;color:#dfe8f5;font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif;padding:14px}
body.embed{padding:6px 10px 10px}
body.embed .pgfmo-top{display:none}
.pgfmo-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.pgfmo-top h1{font-size:18px;font-weight:650;letter-spacing:.3px}
.badge{font-size:12px;padding:3px 9px;border-radius:99px;background:#17233a;border:1px solid #26354f;color:#8fa6c4}
.badge.ok{background:#0e2f22;border-color:#1d6b4a;color:#4ade80}
.badge.bad{background:#331616;border-color:#7f2222;color:#f87171}
.pgfmo-spacer{flex:1}
button{cursor:pointer;border:1px solid #2a3a56;background:#16264a;color:#cfe0f5;padding:6px 12px;border-radius:7px;font-size:14px;font-family:inherit}
button:hover{background:#1d3260;border-color:#3b5686}
button:active{transform:translateY(1px)}
button.pgfmo-sm{padding:4px 9px;font-size:12px}
button.pgfmo-primary{background:#14532d;border-color:#1d6b4a;color:#bbf7d0}
.card{background:#101a2e;border:1px solid #1e2b44;border-radius:11px;padding:14px;margin-bottom:14px}
.card>h2{font-size:14px;font-weight:640;color:#9fb6d6;margin-bottom:11px;letter-spacing:.3px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:10px}
.kv{background:#0c1526;border:1px solid #1b2840;border-radius:8px;padding:9px 11px}
.kv .pgfmo-k{font-size:12.5px;color:#7d93b2;letter-spacing:.4px;text-transform:uppercase}
.kv .pgfmo-v{font-size:16px;font-weight:600;margin-top:3px;word-break:break-all}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.row+.row{margin-top:9px}
.pgfmo-lbl{font-size:13.5px;color:#9fb6d6;min-width:96px}
.dot{width:9px;height:9px;border-radius:99px;background:#4b5563;display:inline-block;flex-shrink:0}
.dot.on{background:#22c55e;box-shadow:0 0 7px #22c55e}
.dot.off{background:#ef4444;box-shadow:0 0 7px #ef4444}
.pgfmo-svcs{display:flex;flex-wrap:wrap;gap:8px}
.svc{display:flex;align-items:center;gap:7px;font-size:12.5px;background:#0c1526;border:1px solid #1b2840;border-radius:8px;padding:6px 10px}
input,select{background:#0c1526;border:1px solid #2a3a56;color:#dfe8f5;padding:6px 9px;border-radius:7px;font-size:14px;font-family:inherit;min-width:110px}
input[type=checkbox]{min-width:0;width:15px;height:15px;accent-color:#22c55e}
input.pgfmo-wide{min-width:210px}
pre{background:#070d18;border:1px solid #1b2840;border-radius:8px;padding:11px;font:12.5px/1.5 Consolas,monospace;color:#9fb6d6;max-height:340px;overflow:auto;white-space:pre-wrap;word-break:break-all}
.hint{font-size:13.5px;color:#7d93b2;margin-top:8px}
.pgfmo-warn{font-size:13.5px;color:#fbbf24;margin-top:8px}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:99;display:none;padding:9px 14px;border-radius:11px;background:rgba(13,21,32,.74);border:1px solid rgba(var(--theme-rgb),.42);color:#eaf2ff;font-size:13.5px;box-shadow:0 10px 28px rgba(0,0,0,.35);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);max-width:min(420px,84vw);text-align:center}
#toast.ok{background:#0e2f22;border-color:#1d6b4a;color:#bbf7d0}
#toast.bad{background:#331616;border-color:#7f2222;color:#fca5a5}


*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1220;color:#dfe8f5;font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif;padding:14px}
body.embed{padding:6px 10px 10px}
.badge{font-size:12px;padding:3px 9px;border-radius:99px;background:#17233a;border:1px solid #26354f;color:#8fa6c4}
.badge.ok{background:#0e2f22;border-color:#1d6b4a;color:#4ade80}
.badge.bad{background:#331616;border-color:#7f2222;color:#f87171}
button{cursor:pointer;border:1px solid #2a3a56;background:#16264a;color:#cfe0f5;padding:6px 12px;border-radius:7px;font-size:14px;font-family:inherit}
button:hover{background:#1d3260}
.card{background:#101a2e;border:1px solid #1e2b44;border-radius:11px;padding:14px;margin-bottom:14px}
.card>h2{font-size:14px;font-weight:640;color:#9fb6d6;margin-bottom:11px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kv{background:#0c1526;border:1px solid #1b2840;border-radius:8px;padding:9px 11px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.row+.row{margin-top:9px}
.hint{font-size:13.5px;color:#7d93b2;margin-top:8px}
input,select{background:#0c1526;border:1px solid #2a3a56;color:#dfe8f5;padding:6px 9px;border-radius:7px;font-size:14px;font-family:inherit;min-width:92px;width:100%}
input[type=checkbox]{min-width:0;width:15px;height:15px;accent-color:#22c55e}
.dot{width:9px;height:9px;border-radius:99px;background:#4b5563;display:inline-block}
.dot.on{background:#22c55e;box-shadow:0 0 7px #22c55e}
.dot.off{background:#ef4444}
.tch{background:#0c1526;border:1px solid #1b2840;border-radius:10px;padding:12px;margin-bottom:10px}
.tch h3{font-size:14px;color:#7dd3fc;margin-bottom:9px;display:flex;align-items:center;gap:8px}
.tsrow{display:flex;gap:14px;flex-wrap:wrap;margin-top:9px;padding-top:9px;border-top:1px dashed #1e2b44}
.tsrow label{display:flex;align-items:center;gap:5px;font-size:13px}
pre{background:#070d18;border:1px solid #1b2840;border-radius:8px;padding:11px;font:12.5px/1.5 Consolas,monospace;color:#9fb6d6;max-height:300px;overflow:auto;white-space:pre-wrap;word-break:break-all}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:99;display:none;padding:9px 14px;border-radius:11px;background:rgba(13,21,32,.74);border:1px solid rgba(var(--theme-rgb),.42);color:#eaf2ff;font-size:13.5px;box-shadow:0 10px 28px rgba(0,0,0,.35);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);max-width:min(420px,84vw);text-align:center}
#toast.ok{background:#0e2f22;border-color:#1d6b4a;color:#bbf7d0}
#toast.bad{background:#331616;border-color:#7f2222;color:#fca5a5}


*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1220;color:#dfe8f5;font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif;padding:14px}
body.embed{padding:6px 10px 10px}
body.embed .pgmulti-top{display:none}
a{color:#4fc3f7;text-decoration:none}
.pgmulti-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.pgmulti-top h1{font-size:18px;font-weight:650;letter-spacing:.3px}
.badge{font-size:12px;padding:3px 9px;border-radius:99px;background:#17233a;border:1px solid #26354f;color:#8fa6c4}
.badge.ok{background:#0e2f22;border-color:#1d6b4a;color:#4ade80}
.badge.bad{background:#331616;border-color:#7f2222;color:#f87171}
.pgmulti-spacer{flex:1}
button{cursor:pointer;border:1px solid #2a3a56;background:#16264a;color:#cfe0f5;padding:6px 12px;border-radius:7px;font-size:14px;font-family:inherit}
button:hover{background:#1d3260;border-color:#3b5686}
button:active{transform:translateY(1px)}
button.pgmulti-sm{padding:4px 9px;font-size:12px}
button.dngr{background:#3a1a1a;border-color:#6e2626;color:#fca5a5}
.card{background:#101a2e;border:1px solid #1e2b44;border-radius:11px;padding:14px;margin-bottom:14px}
.card>h2{font-size:14px;font-weight:640;color:#9fb6d6;margin-bottom:11px;letter-spacing:.3px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:10px}
.kv{background:#0c1526;border:1px solid #1b2840;border-radius:8px;padding:9px 11px}
.kv .k{font-size:12.5px;color:#7d93b2;letter-spacing:.4px;text-transform:uppercase}
.kv .v{font-size:16px;font-weight:600;margin-top:3px;word-break:break-all}
.pgmulti-chs{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px;margin-bottom:14px}
.ch{background:#101a2e;border:1px solid #1e2b44;border-radius:11px;overflow:hidden}
.ch-hd{display:flex;align-items:center;gap:9px;padding:11px 14px;background:linear-gradient(180deg,#17233a,#111c30);border-bottom:1px solid #1e2b44}
.ch-hd .num{font-size:16px;font-weight:680}
.ch-hd .mode{font-size:12px;font-weight:700;padding:3px 9px;border-radius:6px;background:#1b3050;color:#7dd3fc;letter-spacing:.5px}
.ch-bd{padding:13px 14px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.row+.row{margin-top:9px}
select{background:#0c1526;border:1px solid #2a3a56;color:#dfe8f5;padding:6px 9px;border-radius:7px;font-size:14px;font-family:inherit}
.dot{width:9px;height:9px;border-radius:99px;background:#4b5563;display:inline-block;flex-shrink:0}
.dot.on{background:#22c55e;box-shadow:0 0 7px #22c55e}
.dot.off{background:#ef4444;box-shadow:0 0 7px #ef4444}
.st{display:flex;align-items:center;gap:6px;font-size:13.5px;color:#9fb6d6}
.qso{margin-top:10px;padding:8px 10px;background:#0c1526;border:1px solid #1b2840;border-radius:8px;font-size:13.5px;color:#9fb6d6;min-height:34px}
.pgmulti-svcs{display:flex;flex-wrap:wrap;gap:8px}
.svc{display:flex;align-items:center;gap:7px;font-size:12.5px;background:#0c1526;border:1px solid #1b2840;border-radius:8px;padding:6px 10px}
/* ---- 信道规划器 ---- */
.pgmulti-pl{background:linear-gradient(165deg,#111d33,#0b1322);border:1px solid #1f304d;border-radius:12px;padding:15px;margin-bottom:14px;position:relative;overflow:hidden}
.pgmulti-pl:before{content:"";position:absolute;inset:0 0 auto 0;height:2px;background:linear-gradient(90deg,#22d3ee,#3b82f6,#8b5cf6,transparent)}
.pgmulti-pl h2{font-size:14px;font-weight:660;color:#9fb6d6;margin-bottom:13px;display:flex;align-items:center;gap:8px}
.pgmulti-pl h2 .pgmulti-tag{font-size:12px;font-weight:700;letter-spacing:1.2px;color:#22d3ee;background:#0a2733;border:1px solid #155e75;border-radius:5px;padding:2px 7px}
.pgmulti-chips{display:flex;gap:7px;flex-wrap:wrap}
.chip{cursor:pointer;min-width:40px;text-align:center;padding:7px 13px;border-radius:8px;border:1px solid #2a3a56;background:#0c1526;color:#9fb6d6;font-size:14px;font-weight:650;user-select:none;transition:.14s}
.chip:hover{border-color:#3b5686;color:#cfe0f5}
.chip.on{background:linear-gradient(180deg,#0e7490,#155e75);border-color:#22d3ee;color:#e0f7ff;box-shadow:0 0 12px rgba(34,211,238,.28)}
.plrow{display:grid;grid-template-columns:64px 132px 1fr 1fr;gap:9px;align-items:center;padding:8px 10px;background:#0a1322;border:1px solid #1b2840;border-radius:9px;margin-top:8px}
.plrow .n{font-weight:700;color:#7dd3fc;font-size:14.5px;letter-spacing:.3px}
.plrow.dis{opacity:.34}
.pgmulti-plout{margin-top:12px;padding:11px 13px;background:#070d18;border:1px solid #1b2840;border-radius:9px;font:12.5px/1.6 Consolas,monospace;white-space:pre-wrap;word-break:break-all;color:#9fd8b4;max-height:250px;overflow:auto;display:none}
.pgmulti-lbl{font-size:12.5px;color:#7d93b2;letter-spacing:.5px;text-transform:uppercase;margin-bottom:4px}
.pgmulti-f{display:flex;flex-direction:column}
input[type=text],input[type=number]{background:#0c1526;border:1px solid #2a3a56;color:#dfe8f5;padding:7px 10px;border-radius:7px;font-size:14.5px;font-family:inherit;width:100%}
input[type=text]:focus,input[type=number]:focus{outline:none;border-color:#22d3ee;box-shadow:0 0 0 2px rgba(34,211,238,.14)}
input[type=number]{-moz-appearance:textfield}
pre{background:#070d18;border:1px solid #1b2840;border-radius:8px;padding:12px;max-height:430px;overflow:auto;font:12.5px/1.5 Consolas,monospace;white-space:pre-wrap;word-break:break-all;color:#b9cbe4}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:99;display:none;padding:9px 14px;border-radius:11px;background:rgba(13,21,32,.74);border:1px solid rgba(var(--theme-rgb),.42);color:#eaf2ff;font-size:13.5px;box-shadow:0 10px 28px rgba(0,0,0,.35);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);max-width:min(420px,84vw);text-align:center}
#toast.ok{background:#0e2f22;border-color:#1d6b4a;color:#a7f3d0}
#toast.bad{background:#331616;border-color:#7f2222;color:#fecaca}
/* ==================== ★ 整体字号放大 0.5 号 ==================== */
body{font:14.5px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}
.pgmulti-top h1{font-size:18.5px}
.badge{font-size:13.5px}
button{font-size:13.5px}
button.pgmulti-sm{font-size:13.5px}
.card>h2{font-size:14.5px}
.kv .k{font-size:12px}
.kv .v{font-size:15.5px}
.ch-hd .num{font-size:16.5px}
.ch-hd .mode{font-size:13.5px}
.pgmulti-lbl{font-size:12px}
.plrow{grid-template-columns:96px 132px 1fr 1fr}
.plrow .n{font-size:14px}
.plrow .fq{font-size:13px}
input[type=text],input[type=number]{font-size:14px}
select{font-size:13.5px}
.pgmulti-plout,pre{font-size:13px}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:99;display:none;padding:9px 14px;border-radius:11px;background:rgba(13,21,32,.74);border:1px solid rgba(var(--theme-rgb),.42);color:#eaf2ff;font-size:13.5px;box-shadow:0 10px 28px rgba(0,0,0,.35);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);max-width:min(420px,84vw);text-align:center}


/* ========== Light Theme (.light class on body) ========== */
body.light {
  --bg:#e8e3d9 !important;
  --sidebar-bg:#e6e1d7 !important;
  --sidebar-hover:#ded8cc !important;
  --card:#fbfaf7 !important;
  --card-hi:#fdfcf9 !important;
  --card-elev:#e8e3d9 !important;
  --border:rgba(0,0,0,.14) !important;
  --border-lit:rgba(0,0,0,.18) !important;
  --text:#111827 !important;
  --text-2:#475569 !important;
  --muted:#94a3b8 !important;
  --dim:#cbd5e1 !important;
}
body.light,
body.light .sidebar,
body.light .main { background:#f2efe8 !important; }
body.light .card {
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
  box-shadow:0 2px 8px rgba(0,0,0,.04) !important;
}
body.light .card::before { background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.12),transparent) !important; }
body.light .card::after { background:linear-gradient(180deg,transparent,rgba(var(--theme-rgb),.08) 50%,transparent) !important; }
body.light .sb-name { color:#0f172a !important; }
body.light .sb-sub { color:#6b7280 !important; }
body.light .sb-item { color:#374151 !important; }
body.light .sb-item:hover { background:#f8f6f1 !important; color:#0f172a !important; }
body.light .sb-item.active { box-shadow:0 4px 12px rgba(var(--theme-rgb),.2) !important; }
body.light .content,
body.light .card-title,
body.light h1, body.light h2, body.light h3,
body.light .big { color:#0f172a !important; }
body.light .main-top { background:rgba(242,239,232,.98) !important; }
body.light .status-pill {
  background:rgba(var(--theme-rgb),.08) !important;
  color:var(--theme-1) !important;
  border-color:rgba(var(--theme-rgb),.2) !important;
}
body.light input, body.light select, body.light textarea {
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.18) !important;
  color:#0f172a !important;
}
body.light .mc-card {
  background:#fbfaf7 !important; background:#fbfaf7 !important; border-color:rgba(0,0,0,.14) !important; }
body.light .mode-pill { background:rgba(255,255,255,.8) !important; border-color:rgba(0,0,0,.1) !important; color:#374151 !important; }
body.light .mode-pill.active { background:linear-gradient(180deg,var(--theme-2),var(--theme-1)) !important; color:#fff !important; }
body.light .terminal, body.light pre.mini { background:#f8f6f1 !important; color:#1e293b !important; border-color:rgba(0,0,0,.14) !important; }
body.light ::-webkit-scrollbar-thumb { background:var(--dim) !important; }
body.light ::-webkit-scrollbar-track { background:rgba(0,0,0,.03) !important; }
body.light .sidebar { border-right:1px solid rgba(0,0,0,.08) !important; }

/* ========== Font Override ========== */
body, input, select, textarea, button {
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei','Noto Sans CJK SC',system-ui,sans-serif !important;
}
code, pre, .mono, .terminal {
  font-family:ui-monospace,'SFMono-Regular','Cascadia Mono','Noto Sans Mono',Menlo,Consolas,'Courier New',monospace !important;
}

/* ========== Theme Toggle Button ========== */
.theme-toggle-btn {
  display:inline-flex;align-items:center;justify-content:center;
  width:30px;height:30px;border-radius:6px;
  border:1px solid var(--border-lit);
  background:transparent;color:var(--text-2);
  cursor:pointer;font-size:15px;
  transition:all .18s;flex-shrink:0;
  opacity:.7;
}
.theme-toggle-btn:hover {
  opacity:1;
  border-color:rgba(var(--theme-rgb),.5);
  color:var(--theme-2);
  background:rgba(var(--theme-rgb),.08);
}

/* ========== Better dark mode shadows ========== */
body:not(.light) .card {
  box-shadow:0 4px 20px rgba(0,0,0,.25), 0 0 0 1px rgba(255,255,255,.03);
}

/* ================================================================
   COMPREHENSIVE LIGHT THEME - All components properly styled
   ================================================================ */
:root {
  --bg-input:#0a121d;
}
body.light {
  /* Core palette */
  --bg:#e8e3d9 !important;
  --bg-input:#fbfaf7 !important;
  --sidebar-bg:#e6e1d7 !important;
  --sidebar-hover:#ded8cc !important;
  --card:#fbfaf7 !important;
  --card-hi:#fdfcf9 !important;
  --card-elev:#e8e3d9 !important;
  --border:rgba(0,0,0,.14) !important;
  --border-lit:rgba(0,0,0,.18) !important;
  --text:#111827 !important;
  --text-2:#1f2937 !important;
  --muted:#4b5563 !important;
  --dim:#6b7280 !important;
}

/* --- Body & Layout --- */
body.light,
body.light .sidebar,
body.light .main,
body.light .content {
  background:#f2efe8 !important;
  color:#0f172a !important;
}
body.light .main-top {
  background:rgba(242,239,232,.98) !important;
  border-bottom-color:rgba(0,0,0,.06) !important;
}
body.light::before {
  background:
    radial-gradient(1200px 600px at 12% -10%, rgba(16,185,129,.04), transparent 60%),
    radial-gradient(900px 500px at 88% 0%, rgba(56,189,248,.04), transparent 55%) !important;
}
body.light::after {
  background:linear-gradient(90deg,transparent,rgba(56,189,248,.15),rgba(52,211,153,.15),rgba(139,92,246,.15),transparent) !important;
  opacity:.3 !important;
}

/* --- Sidebar --- */
body.light .sidebar {
  border-right-color:rgba(0,0,0,.08) !important;
}
body.light .sb-name { color:#0f172a !important; }
body.light .sb-sub { color:#4b5563 !important; }
body.light .sb-item { color:#374151 !important; }
body.light .sb-item:hover {
  background:rgba(0,0,0,.04) !important;
  color:#0f172a !important;
}
body.light .sb-item.active {
  background:linear-gradient(135deg,var(--theme-2),var(--theme-1),var(--theme-3)) !important;
  color:#fff !important;
  box-shadow:0 4px 12px rgba(var(--theme-rgb),.25) !important;
}
body.light .sb-brand { border-bottom-color:rgba(0,0,0,.06) !important; }
body.light .sb-brand::after {
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.15),transparent) !important;
}

/* --- Cards --- */
body.light .card {
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
  box-shadow:0 1px 4px rgba(0,0,0,.07), 0 0 0 1px rgba(0,0,0,.04) !important;
}
body.light .card:hover {
  border-color:rgba(0,0,0,.18) !important;
  box-shadow:0 4px 14px rgba(0,0,0,.1), 0 0 0 1px rgba(0,0,0,.05) !important;
}
body.light .card::before {
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.12),transparent) !important;
}
body.light .card::after {
  background:linear-gradient(180deg,transparent,rgba(var(--theme-rgb),.08) 50%,transparent) !important;
}
body.light .card-title,
body.light .card-head h2,
body.light .card-head h3 { color:#0f172a !important; }
body.light .card-sub { color:#4b5563 !important; }
body.light .card-badge {
  background:rgba(var(--theme-rgb),.08) !important;
  color:var(--theme-1) !important;
  border-color:rgba(var(--theme-rgb),.2) !important;
}

/* --- Text hierarchy --- */
body.light h1, body.light h2, body.light h3, body.light h4 { color:#0f172a !important; }
body.light .big { color:#0f172a !important; }
body.light .stat-lbl { color:#4b5563 !important; }
body.light .stat-val { color:#0f172a !important; }
body.light .mc-mode-txt { color:#0f172a !important; }
body.light .mc-off { color:#4b5563 !important; }
body.light .mc-hd { color:#0f172a !important; }
body.light .mc-freq { color:#0f172a !important; }
body.light .mc-empty { color:#6b7280 !important; }
body.light .mc-tip { color:#6b7280 !important; }

/* --- Status indicators --- */
body.light .cyan { color:#0891b2 !important; }
body.light .green { color:#059669 !important; }
body.light .red { color:#dc2626 !important; }
body.light .amber { color:#d97706 !important; }
body.light .status-pill {
  background:rgba(var(--theme-rgb),.06) !important;
  color:var(--theme-1) !important;
  border-color:rgba(var(--theme-rgb),.15) !important;
}

/* --- Mode pills --- */
body.light .mode-pill {
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.1) !important;
  color:#374151 !important;
}
body.light .mode-pill:hover {
  border-color:rgba(var(--theme-rgb),.4) !important;
  color:#0f172a !important;
  background:rgba(var(--theme-rgb),.04) !important;
}
body.light .mode-pill.active {
  background:linear-gradient(180deg,var(--theme-2),var(--theme-1)) !important;
  color:#fff !important;
  border-color:var(--theme-1) !important;
}

/* --- MC Cards --- */
body.light .mc-card {
  background:#fbfaf7 !important;
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
}
body.light .mc-card.on { border-color:rgba(16,185,129,.4) !important; }

/* --- QSO items --- */
body.light .qso-item {
  background:#fbfaf7 !important;
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
}
body.light .qso-item:hover {
  border-color:rgba(var(--theme-rgb),.3) !important;
  box-shadow:0 4px 12px rgba(0,0,0,.06) !important;
}
body.light .qso-call { 
  -webkit-text-fill-color:var(--theme-1) !important;
  color:var(--theme-1) !important;
}
body.light .qso-country {
  background:rgba(0,0,0,.04) !important;
  color:#0f172a !important;
  border-color:rgba(0,0,0,.14) !important;
}
body.light .qso-id {
  background:rgba(59,130,246,.08) !important;
  color:#2563eb !important;
  border-color:rgba(59,130,246,.2) !important;
}
body.light .qso-time {
  background:rgba(var(--theme-rgb),.06) !important;
  color:#374151 !important;
  border-color:rgba(var(--theme-rgb),.15) !important;
}
body.light .qso-row2 { color:#374151 !important; border-top-color:rgba(0,0,0,.06) !important; }
body.light .qso-row2 b { color:#0f172a !important; }
body.light .qso-prev { color:#4b5563 !important; }
body.light .qso-prev b { color:#0f172a !important; }
body.light .qso-empty { color:#6b7280 !important; }
body.light .qso-cell-empty {
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
  color:#6b7280 !important;
}
body.light .qso-tag.mode {
  background:rgba(var(--theme-rgb),.08) !important;
  color:var(--theme-1) !important;
  border-color:rgba(var(--theme-rgb),.2) !important;
}
body.light .qso-tag.src-rf {
  background:rgba(59,130,246,.08) !important;
  color:#2563eb !important;
  border-color:rgba(59,130,246,.2) !important;
}
body.light .qso-tag.src-net {
  background:rgba(16,185,129,.08) !important;
  color:#059669 !important;
  border-color:rgba(16,185,129,.2) !important;
}
body.light .qso-tag.src-local {
  background:rgba(139,92,246,.08) !important;
  color:#7c3aed !important;
  border-color:rgba(139,92,246,.2) !important;
}
body.light .qso-slot-state.live {
  background:rgba(239,68,68,.08) !important;
  color:#dc2626 !important;
  border-color:rgba(239,68,68,.2) !important;
}
body.light .qso-slot-state.idle {
  background:rgba(16,185,129,.08) !important;
  color:#059669 !important;
  border-color:rgba(16,185,129,.2) !important;
}
body.light .qso-live-badge {
  background:rgba(239,68,68,.08) !important;
  color:#dc2626 !important;
  border-color:rgba(239,68,68,.2) !important;
}
body.light .qso-item.qso-live {
  background:rgba(239,68,68,.04) !important;
  border-color:rgba(239,68,68,.3) !important;
  box-shadow:0 0 0 1px rgba(239,68,68,.15) !important;
}
body.light .ber.good {
  background:rgba(16,185,129,.08) !important;
  color:#059669 !important;
  border-color:rgba(16,185,129,.2) !important;
}
body.light .ber.warn {
  background:rgba(245,158,11,.08) !important;
  color:#d97706 !important;
  border-color:rgba(245,158,11,.2) !important;
}
body.light .ber.bad {
  background:rgba(239,68,68,.08) !important;
  color:#dc2626 !important;
  border-color:rgba(239,68,68,.2) !important;
}
/* 日间模式下，通联列表的说明性文字全部使用高对比黑色 */
body.light .qso-ch-head,
body.light .qso-slot-lbl,
body.light #qsoPanel .qso-slot-lbl,
body.light #qsoPanel .qso-ch-head,
body.light #qsoPanel .qso-ch-cnt,
body.light #qsoPanel .qso-cell-empty,
body.light #qsoPanel .qso-empty {
  color:#111827 !important;
}

/* --- Stat cards --- */
body.light .stat {
  background:#fbfaf7 !important;
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
}
body.light .stat::before {
  background:linear-gradient(90deg,transparent,rgba(var(--theme-rgb),.1),transparent) !important;
}

/* --- QSO table --- */
body.light .qso-table th {
  background:#f1f5f9 !important;
  color:#374151 !important;
  border-bottom-color:rgba(0,0,0,.08) !important;
}
body.light .qso-table td { border-bottom-color:rgba(0,0,0,.1) !important; color:#111827 !important; }
body.light .qso-table tbody tr:nth-child(even) td { background:rgba(0,0,0,.02) !important; }
body.light .qso-table tbody tr:hover td { background:rgba(59,130,246,.04) !important; }
body.light .qso-table td.mono { color:#374151 !important; }

/* --- Port cards --- */
body.light .port-card {
  background:#fbfaf7 !important;
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
}
body.light .port-info b { color:#0f172a !important; }
body.light .port-info span { color:#4b5563 !important; }

/* --- DB cards --- */
body.light .db-card {
  background:#fbfaf7 !important;
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
}
body.light .db-card h3 { color:#0f172a !important; }
body.light .db-card p { color:#4b5563 !important; }
body.light .db-bar {
  background:linear-gradient(90deg,rgba(var(--theme-rgb),.06),rgba(var(--theme-rgb),.02),transparent) !important;
  border-color:rgba(var(--theme-rgb),.15) !important;
}
body.light .db-bar-txt b { color:#0f172a !important; }
body.light .db-bar-txt span { color:#4b5563 !important; }

/* --- Buttons --- */
body.light .btn-ghost {
  background:#fbfaf7 !important;
  color:#374151 !important;
  border-color:rgba(0,0,0,.18) !important;
}
body.light .btn-ghost:hover {
  background:#fbfaf7 !important;
  color:#0f172a !important;
}
body.light .refresh-btn {
  background:#fbfaf7 !important;
  color:#374151 !important;
  border-color:rgba(0,0,0,.18) !important;
}

/* --- Forms --- */
body.light input,
body.light select,
body.light textarea {
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.18) !important;
  color:#0f172a !important;
}
body.light input:focus,
body.light select:focus,
body.light textarea:focus {
  border-color:var(--theme-1) !important;
  box-shadow:0 0 0 3px rgba(var(--theme-rgb),.08) !important;
}
body.light select option {
  background:#fbfaf7 !important;
  color:#0f172a !important;
}
body.light .form-grid label { color:#374151 !important; }
body.light .form-grid .hint { color:#6b7280 !important; }
body.light .form-grid input,
body.light .form-grid select {
  background:#fbfaf7 !important;
  color:#0f172a !important;
  border-color:rgba(0,0,0,.18) !important;
}
body.light .preset-box select {
  background:#fbfaf7 !important;
  color:#0f172a !important;
}

/* --- Freq banner --- */
body.light .freq-banner {
  background:linear-gradient(90deg,rgba(var(--theme-rgb),.04),#f8f9fb 60%,#f8f9fb) !important;
  background:linear-gradient(90deg,rgba(var(--theme-rgb),.04),#ffffff 60%,#ffffff) !important;
  border-color:rgba(var(--theme-rgb),.12) !important;
}
body.light .freq-banner-txt > b { color:#0f172a !important; }
body.light .freq-banner-txt > span { color:#374151 !important; }
body.light .freq-banner-txt > span > b { color:#0f172a !important; }

/* --- Freq chips/rows --- */
body.light .freq-chip {
  background:#fbfaf7 !important;
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
  color:#374151 !important;
}
body.light .freq-chip b { color:#059669 !important; }
body.light .freq-row {
  background:#fbfaf7 !important;
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
}
body.light .freq-row .fr-n { color:#374151 !important; }
body.light .freq-strip {
  background:#f2efe8 !important;
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
  color:#374151 !important;
}
body.light .freq-strip::before { color:#059669 !important; }

/* --- BM bar --- */
body.light .bm-bar {
  background:#fbfaf7 !important;
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
  color:#374151 !important;
}
body.light .bm-bar.ok { border-color:rgba(16,185,129,.3) !important; color:#059669 !important; }
body.light .bm-bar.bad { border-color:rgba(239,68,68,.3) !important; color:#dc2626 !important; }

/* --- Console / Log --- */
body.light .console {
  background:#fbfaf7 !important;
  color:#1e293b !important;
  border-color:rgba(0,0,0,.14) !important;
}
body.light .console .tag-in { color:#059669 !important; }

/* --- Smart selector --- */
body.light .sp {
  background:#fbfaf7 !important;
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.14) !important;
}
body.light .sp-head {
  background:rgba(0,0,0,.02) !important;
  border-bottom-color:rgba(0,0,0,.06) !important;
}
body.light .sp-title { color:#374151 !important; }
body.light .sp-hint { color:#6b7280 !important; }
body.light .sp-in {
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.1) !important;
  color:#0f172a !important;
}
body.light .sp-in::placeholder { color:#6b7280 !important; }
body.light .sp-cnt { color:#0891b2 !important; }
body.light .sp-chip {
  background:rgba(0,0,0,.04) !important;
  border-color:rgba(0,0,0,.14) !important;
  color:#374151 !important;
}
body.light .sp-chip:hover { background:rgba(56,189,248,.08) !important; color:#0f172a !important; }
body.light .sp-chip.on {
  background:rgba(16,185,129,.08) !important;
  border-color:rgba(16,185,129,.3) !important;
  color:#059669 !important;
}
body.light .sp-row:hover { background:rgba(0,0,0,.03) !important; }
body.light .sp-row .nm { color:#111827 !important; }
body.light .sp-row .hs { color:#374151 !important; }
body.light .sp-row .pt { color:#6b7280 !important; }
body.light .sp-empty { color:#6b7280 !important; }
body.light .sp-sel { color:#059669 !important; border-top-color:rgba(0,0,0,.06) !important; }
body.light .sp-sel b { color:#0f172a !important; }
body.light .sp-sel .none { color:#d97706 !important; }
body.light .sp-go {
  background:rgba(56,189,248,.08) !important;
  border-color:rgba(56,189,248,.2) !important;
  color:#0891b2 !important;
}
body.light .sp-mod label { color:#374151 !important; }
body.light .sp-mod select {
  background:#fbfaf7 !important;
  border-color:rgba(0,0,0,.1) !important;
  color:#0f172a !important;
}
body.light .essid-note { color:#6b7280 !important; }
body.light .essid-note b { color:#059669 !important; }

/* --- Network info --- */
body.light #sysInfo,
body.light #sysNet { color:#0f172a !important; }
body.light #sysInfo b, body.light #sysNet b,
body.light #sysInfo span, body.light #sysNet span { color:#0f172a !important; }
body.light #sysInfo .kvw { color:#4b5563 !important; }
body.light .netlab { color:#4b5563 !important; }
body.light .netval { color:#0f172a !important; }
body.light .netval.off { color:#6b7280 !important; }
body.light .netmeta { color:#374151 !important; }
body.light .netmeta-k { color:#4b5563 !important; }
body.light .netsep { color:#cbd5e1 !important; }
body.light #sysNet .dimtxt { color:#4b5563 !important; }

/* --- Footer --- */
body.light .footer { border-top-color:rgba(0,0,0,.06) !important; color:#4b5563 !important; }

/* --- Toast --- */
body.light #toast.ok {
  background:#e6f7ee !important;
  border-color:#a7f3d0 !important;
  color:#065f46 !important;
}
body.light #toast.bad {
  background:#fde8e8 !important;
  border-color:#fecaca !important;
  color:#991b1b !important;
}

/* --- Scrollbar --- */
body.light ::-webkit-scrollbar-thumb { background:rgba(0,0,0,.15) !important; }
body.light ::-webkit-scrollbar-track { background:rgba(0,0,0,.02) !important; }

/* --- Save buttons (keep colorful in light mode) --- */
body.light .save-btn { color:#fff !important; }

/* --- Theme toggle --- */
body.light .theme-toggle-btn {
  border-color:rgba(0,0,0,.1) !important;
  color:#4b5563 !important;
}
body.light .theme-toggle-btn:hover {
  border-color:rgba(var(--theme-rgb),.3) !important;
  color:var(--theme-1) !important;
  background:rgba(var(--theme-rgb),.06) !important;
}


/* ================================================================
   PREMIUM VISUAL POLISH - Animations, Glass, Depth
   ================================================================ */

/* --- Smooth page transitions --- */
.proto-page {
  /* animation removed */
}
@keyframes fadeUp {
  from { opacity:0; transform:translateY(8px); }
  to { opacity:1; transform:none; }
}

/* --- Card hover lift --- */
.card {
  transition:border-color .2s, box-shadow .2s, transform .2s;
}
.card:hover {
  transform:translateY(-1px);
  box-shadow:0 8px 24px rgba(0,0,0,.2), 0 0 0 1px rgba(255,255,255,.03);
}

/* --- Button press effect --- */
.btn:active,
.save-btn:active,
.mode-pill:active {
  transform:scale(.97) !important;
}

/* --- Smooth scroll --- */
.main { scroll-behavior:smooth; }

/* --- Status pill glow --- */
.status-pill .dot {
  animation:pulse-glow 2s ease-in-out infinite;
}
@keyframes pulse-glow {
  0%,100% { opacity:1; box-shadow:0 0 8px var(--theme-2); }
  50% { opacity:.6; box-shadow:0 0 16px var(--theme-2); }
}

/* --- Stat card number animation --- */
.stat-val {
  transition:color .3s;
}

/* --- Sidebar item indicator --- */
.sb-item.active::before {
  content:'';
  position:absolute;
  left:0;top:50%;transform:translateY(-50%);
  width:3px;height:60%;
  background:var(--theme-2);
  border-radius:0 2px 2px 0;
  box-shadow:0 0 8px var(--theme-2);
}

/* --- Better focus rings --- */
input:focus-visible,
select:focus-visible,
textarea:focus-visible,
button:focus-visible {
  outline:2px solid rgba(var(--theme-rgb),.5);
  outline-offset:2px;
}

/* --- Smooth select transitions --- */
select {
  transition:border-color .15s, box-shadow .15s;
}

/* --- QSO card stagger animation --- */
.qso-item {
  /* animation removed - was causing flicker on refresh */
}

@keyframes fadeIn {
  from { opacity:0; transform:translateY(6px); }
  to { opacity:1; transform:none; }
}

/* --- Scan line (already exists, just enhance) --- */
@keyframes scanX {
  0% { transform:translateX(-100%); }
  100% { transform:translateX(100%); }
}

/* --- Mode pill shimmer on hover --- */
.mode-pill::before {
  content:'';
  position:absolute;inset:0;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.06),transparent);
  transform:translateX(-100%);
  transition:transform .5s;
}
.mode-pill:hover::before {
  transform:translateX(100%);
}

/* --- Better scrollbar --- */
::-webkit-scrollbar { width:6px; height:6px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb {
  background:rgba(255,255,255,.12);
  border-radius:3px;
}
::-webkit-scrollbar-thumb:hover {
  background:rgba(255,255,255,.2);
}

/* --- Loading spinner --- */
@keyframes spin {
  to { transform:rotate(360deg); }
}
.loading::after {
  content:'';
  display:inline-block;
  width:14px;height:14px;
  border:2px solid rgba(var(--theme-rgb),.3);
  border-top-color:var(--theme-2);
  border-radius:50%;
  animation:spin .6s linear infinite;
  margin-left:8px;
  vertical-align:middle;
}

@media(max-width:760px){
  #tgSrvCards{grid-template-columns:1fr !important;}
  #tgSrvForm{grid-template-columns:1fr 1fr !important;}
  #tgSrvForm > div:last-child{grid-column:1/-1;justify-content:flex-start !important;}
}
@media(max-width:460px){
  #tgSrvForm{grid-template-columns:1fr !important;}
  #tgSrvForm > div:last-child{grid-column:auto;}
}

/* ARCH FINAL LIGHT THEME OVERRIDE (after redesign v2) */
body.arch-ui-v2.light{--arch-bg:#f2efe8;--arch-panel:#fbfaf7;--arch-panel-2:#f5f2eb;--arch-line:rgba(0,0,0,.12);--arch-text:#111827;--arch-muted:#4b5563;--arch-cyan:#0891b2;--arch-mint:#059669;--arch-violet:#7c3aed;--arch-amber:#b45309;--arch-danger:#dc2626;--arch-shadow:0 10px 30px rgba(0,0,0,.08);background:radial-gradient(1000px 620px at 12% -8%,rgba(8,145,178,.06),transparent 60%),radial-gradient(820px 520px at 92% 4%,rgba(124,58,237,.05),transparent 62%),#f2efe8!important;color:#111827!important}
body.arch-ui-v2.light::before{background-image:linear-gradient(rgba(15,23,42,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(15,23,42,.025) 1px,transparent 1px)!important}
body.arch-ui-v2.light::after{background:radial-gradient(520px 300px at 74% 18%,rgba(8,145,178,.05),transparent 70%)!important;opacity:.8!important}
body.arch-ui-v2.light .sidebar{background:linear-gradient(180deg,#e8e3d9,#f1ede6)!important;border-right-color:rgba(0,0,0,.1)!important;box-shadow:10px 0 36px rgba(0,0,0,.08)!important}
body.arch-ui-v2.light .sb-name,body.arch-ui-v2.light .arch-topbar-brand b{color:#0f172a!important}
body.arch-ui-v2.light .sb-sub,body.arch-ui-v2.light .arch-topbar-brand small,body.arch-ui-v2.light .arch-topbar-clock{color:#4b5563!important}
body.arch-ui-v2.light .sb-item{color:#374151!important}
body.arch-ui-v2.light .sb-item:hover{background:rgba(0,0,0,.045)!important;border-color:rgba(0,0,0,.08)!important;color:#0f172a!important}
body.arch-ui-v2.light .sb-item.active{background:linear-gradient(135deg,var(--theme-2),var(--theme-1),var(--theme-3))!important;border-color:transparent!important;color:#fff!important}
body.arch-ui-v2.light .card,body.arch-ui-v2.light .stat,body.arch-ui-v2.light .form-card,body.arch-ui-v2.light .freq-strip,body.arch-ui-v2.light .mc-card{background:#fbfaf7!important;border-color:rgba(0,0,0,.13)!important;box-shadow:0 6px 20px rgba(0,0,0,.07)!important}
body.arch-ui-v2.light .card:hover,body.arch-ui-v2.light .stat:hover,body.arch-ui-v2.light .mc-card:hover{border-color:rgba(8,145,178,.28)!important;box-shadow:0 12px 30px rgba(0,0,0,.11)!important}
body.arch-ui-v2.light .arch-topbar,body.arch-ui-v2.light .main-top{background:rgba(242,239,232,.97)!important;border-bottom-color:rgba(0,0,0,.09)!important;color:#0f172a!important}
body.arch-ui-v2.light .arch-topbar-status{background:rgba(8,145,178,.08)!important;border-color:rgba(8,145,178,.2)!important;color:#0e7490!important}
body.arch-ui-v2.light .arch-topbar-btn{background:#fff!important;border-color:rgba(0,0,0,.14)!important;color:#334155!important}
body.arch-ui-v2.light .card-title,body.arch-ui-v2.light h1,body.arch-ui-v2.light h2,body.arch-ui-v2.light h3,body.arch-ui-v2.light h4,body.arch-ui-v2.light .stat-val,body.arch-ui-v2.light .big,body.arch-ui-v2.light .mc-freq,body.arch-ui-v2.light .mc-hd{color:#0f172a!important}
body.arch-ui-v2.light .card-sub,body.arch-ui-v2.light .help,body.arch-ui-v2.light .hint,body.arch-ui-v2.light .stat-lbl,body.arch-ui-v2.light .mc-tip,body.arch-ui-v2.light .mc-off,body.arch-ui-v2.light .mc-empty{color:#4b5563!important}
body.arch-ui-v2.light .btn,body.arch-ui-v2.light button.btn,body.arch-ui-v2.light .mode-pill,body.arch-ui-v2.light .mc-row .btn{background:#fbfaf7!important;border-color:rgba(0,0,0,.14)!important;color:#334155!important}
body.arch-ui-v2.light .btn.p,body.arch-ui-v2.light .btn.btn-green,body.arch-ui-v2.light .btn.primary,body.arch-ui-v2.light .mode-pill.active{background:linear-gradient(135deg,var(--theme-2),var(--theme-1))!important;border-color:transparent!important;color:#fff!important}
body.arch-ui-v2.light input,body.arch-ui-v2.light select,body.arch-ui-v2.light textarea,body.arch-ui-v2.light #dmrBmHost{background:#fff!important;border-color:rgba(0,0,0,.18)!important;color:#0f172a!important}
body.arch-ui-v2.light input::placeholder,body.arch-ui-v2.light textarea::placeholder{color:#94a3b8!important}
body.arch-ui-v2.light .badge,body.arch-ui-v2.light .chip{background:rgba(8,145,178,.08)!important;border-color:rgba(8,145,178,.2)!important;color:#0e7490!important}
body.arch-ui-v2.light .badge.b-ok,body.arch-ui-v2.light .badge.ok{background:rgba(5,150,105,.1)!important;border-color:rgba(5,150,105,.24)!important;color:#047857!important}
body.arch-ui-v2.light .badge.b-bad,body.arch-ui-v2.light .badge.bad{background:rgba(220,38,38,.09)!important;border-color:rgba(220,38,38,.22)!important;color:#b91c1c!important}
body.arch-ui-v2.light .tbl th{background:#f5f2eb!important;color:#0f172a!important;border-bottom-color:rgba(0,0,0,.12)!important}
body.arch-ui-v2.light .tbl td{border-bottom-color:rgba(0,0,0,.08)!important;color:#1f2937!important}
body.arch-ui-v2.light .tbl tr:hover td{background:rgba(8,145,178,.04)!important}
body.arch-ui-v2.light .status-pill{background:rgba(8,145,178,.08)!important;border-color:rgba(8,145,178,.2)!important;color:#0e7490!important}
body.arch-ui-v2.light ::-webkit-scrollbar-track{background:rgba(0,0,0,.03)!important}
body.arch-ui-v2.light ::-webkit-scrollbar-thumb{background:linear-gradient(180deg,rgba(8,145,178,.35),rgba(124,58,237,.25))!important}
</style>
<style id="fmStyleUnify">
/* ===== FM 联网配置：统一到当前控制台风格 ===== */
#page-fmo .pgfmo-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
#page-fmo .pgfmo-top h1{font-size:20px;font-weight:800;color:#fff;letter-spacing:.2px;margin:0}
#page-fmo .card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 18px;margin-bottom:14px}
#page-fmo .card h2{font-size:17px;font-weight:800;color:#fff;margin:0 0 12px;padding-left:10px;border-left:3px solid var(--theme-2);line-height:1.25}
#page-fmo .grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
#page-fmo .kv{display:block;background:var(--card-hi);border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin:0}
#page-fmo .pgfmo-k{font-size:12.5px;color:var(--muted);font-weight:700;letter-spacing:.4px;text-transform:none}
#page-fmo .pgfmo-v{font-size:15px;font-weight:600;color:var(--text);margin-top:4px;word-break:break-all}
#page-fmo .hint{font-size:13.5px;color:var(--muted);line-height:1.75;margin-top:9px}
#page-fmo .pgfmo-warn{font-size:13.5px;color:#fbbf24;line-height:1.75;margin-top:8px}
#page-fmo input,#page-fmo select{background:var(--card-hi);border:1px solid var(--border);border-radius:9px;color:var(--text);padding:9px 11px;width:100%;font-size:14px;outline:none;box-sizing:border-box}
#page-fmo input:focus,#page-fmo select:focus{border-color:var(--theme-2)}
#page-fmo button.pgfmo-sm{background:var(--card-hi);border:1px solid var(--border);border-radius:9px;color:var(--text);padding:8px 14px;font-size:13.5px;cursor:pointer;transition:.15s}
#page-fmo button.pgfmo-sm:hover{border-color:var(--theme-2);color:var(--theme-2)}
#page-fmo button.pgfmo-primary{background:rgba(var(--theme-rgb),.16);border-color:rgba(var(--theme-rgb),.45);color:var(--theme-2);font-weight:700}
#page-fmo .pgfmo-svcs .kv{min-width:150px}
</style>
<style id="arch-redesign-116">
/* ARCH Redesign 1.0.118 — Aurora Command Center */
:root{
  --arch-bg:#05070c;
  --arch-panel:rgba(14,19,29,.78);
  --arch-panel-strong:rgba(18,25,38,.92);
  --arch-line:rgba(148,163,184,.14);
  --arch-line-hot:rgba(56,189,248,.35);
  --arch-text:#eef5ff;
  --arch-muted:#8ea0b8;
  --arch-cyan:#43d9ff;
  --arch-mint:#48f0b2;
  --arch-violet:#9b7bff;
  --arch-amber:#ffc766;
  --arch-danger:#ff5d7a;
  --arch-radius:22px;
  --arch-shadow:0 18px 60px rgba(0,0,0,.42);
}
body.arch-ui-v2{
  background:
    radial-gradient(1000px 620px at 12% -8%,rgba(67,217,255,.16),transparent 60%),
    radial-gradient(820px 520px at 92% 4%,rgba(155,123,255,.13),transparent 62%),
    radial-gradient(760px 520px at 50% 112%,rgba(72,240,178,.08),transparent 60%),
    var(--arch-bg)!important;
  color:var(--arch-text)!important;
  font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif!important;
  letter-spacing:.01em;
}
body.arch-ui-v2::before{
  content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:linear-gradient(rgba(255,255,255,.022) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.022) 1px,transparent 1px);
  background-size:46px 46px;mask-image:linear-gradient(to bottom,rgba(0,0,0,.75),transparent 92%);
}
body.arch-ui-v2::after{
  content:"";position:fixed;inset:0;pointer-events:none;z-index:1;opacity:.5;
  background:radial-gradient(520px 300px at 74% 18%,rgba(67,217,255,.06),transparent 70%);
}
body.arch-ui-v2 .sidebar{
  width:252px!important;background:linear-gradient(180deg,rgba(16,22,34,.96),rgba(8,12,20,.94))!important;
  border-right:1px solid var(--arch-line)!important;box-shadow:18px 0 70px rgba(0,0,0,.34)!important;
  backdrop-filter:blur(22px) saturate(135%);z-index:60!important;
}
body.arch-ui-v2 .sb-brand{padding:22px 18px 18px!important;border-bottom:1px solid var(--arch-line)!important}
body.arch-ui-v2 .sb-icon{
  width:42px!important;height:42px!important;border-radius:14px!important;
  background:conic-gradient(from 220deg,var(--arch-cyan),var(--arch-mint),var(--arch-violet),var(--arch-cyan))!important;
  color:#031018!important;font-size:20px!important;box-shadow:0 0 34px rgba(67,217,255,.34)!important;
  animation:archOrb 8s linear infinite;
}
body.arch-ui-v2 .sb-name{font-size:17px!important;letter-spacing:.04em!important}
body.arch-ui-v2 .sb-sub{font-size:12px!important;color:var(--arch-muted)!important}
body.arch-ui-v2 .sb-nav{padding:14px 10px!important;gap:4px!important}
body.arch-ui-v2 .sb-item{
  min-height:46px!important;border:1px solid transparent!important;border-radius:14px!important;
  color:#b9c7dc!important;font-size:14px!important;font-weight:650!important;
  transition:transform .22s cubic-bezier(.2,.8,.2,1),background .22s,border-color .22s,color .22s,box-shadow .22s!important;
}
body.arch-ui-v2 .sb-item:hover{transform:translateX(3px)!important;background:rgba(67,217,255,.07)!important;border-color:rgba(67,217,255,.16)!important;color:#fff!important}
body.arch-ui-v2 .sb-item.active{
  background:linear-gradient(135deg,rgba(67,217,255,.22),rgba(155,123,255,.16))!important;
  border-color:rgba(67,217,255,.32)!important;color:#fff!important;
  box-shadow:0 10px 30px rgba(67,217,255,.13),inset 0 1px 0 rgba(255,255,255,.1)!important;
}
body.arch-ui-v2 .sb-item .ico{filter:drop-shadow(0 0 8px rgba(67,217,255,.28))}
body.arch-ui-v2 .main{background:transparent!important}
body.arch-ui-v2 .content{max-width:1480px!important;margin:0 auto!important;padding:22px 26px 64px!important;width:100%!important}
.arch-topbar{
  position:sticky;top:0;z-index:80;display:flex;align-items:center;gap:14px;
  min-height:68px;padding:0 24px;margin:0;
  background:rgba(7,11,18,.82);border-bottom:1px solid var(--arch-line);
  backdrop-filter:blur(22px) saturate(150%);-webkit-backdrop-filter:blur(22px) saturate(150%);
  color:var(--arch-text);font:600 14px/1.2 ui-sans-serif,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
}
.arch-topbar-brand{display:flex;align-items:center;gap:11px;min-width:max-content}
.arch-orb{width:13px;height:13px;border-radius:50%;background:var(--arch-mint);box-shadow:0 0 0 5px rgba(72,240,178,.1),0 0 24px rgba(72,240,178,.8);animation:archPulse 2.4s ease-in-out infinite}
.arch-topbar-brand b{display:block;font-size:15px;letter-spacing:.08em;color:#fff}
.arch-topbar-brand small{display:block;font-size:11px;color:var(--arch-muted);letter-spacing:.12em;text-transform:uppercase}
.arch-topbar-status{display:flex;align-items:center;gap:8px;padding:8px 13px;border:1px solid rgba(67,217,255,.18);border-radius:999px;background:rgba(67,217,255,.06);color:#bfefff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:38vw}
.arch-status-dot{width:7px;height:7px;border-radius:50%;background:var(--arch-cyan);box-shadow:0 0 14px var(--arch-cyan);animation:archPulse 2s ease-in-out infinite}
.arch-topbar-spacer{flex:1}
.arch-topbar-clock{font-family:var(--mono,ui-monospace,monospace);font-size:12px;color:var(--arch-muted);letter-spacing:.08em}
.arch-topbar-btn{width:42px;height:42px;border:1px solid var(--arch-line);border-radius:14px;background:rgba(255,255,255,.04);color:#dceeff;font-size:19px;cursor:pointer;transition:transform .2s,background .2s,border-color .2s,box-shadow .2s}
.arch-topbar-btn:hover{transform:translateY(-2px);background:rgba(67,217,255,.1);border-color:rgba(67,217,255,.34);box-shadow:0 10px 26px rgba(67,217,255,.12)}
body.arch-ui-v2 .card,
body.arch-ui-v2 .stat,
body.arch-ui-v2 .form-card,
body.arch-ui-v2 .freq-strip,
body.arch-ui-v2 .mc-card{
  background:linear-gradient(145deg,rgba(20,28,43,.82),rgba(10,15,24,.88))!important;
  border:1px solid var(--arch-line)!important;border-radius:var(--arch-radius)!important;
  box-shadow:var(--arch-shadow),inset 0 1px 0 rgba(255,255,255,.045)!important;
  transition:transform .28s cubic-bezier(.2,.8,.2,1),border-color .28s,box-shadow .28s!important;
}
body.arch-ui-v2 .card:hover,
body.arch-ui-v2 .stat:hover,
body.arch-ui-v2 .mc-card:hover{transform:translateY(-3px)!important;border-color:rgba(67,217,255,.22)!important;box-shadow:0 24px 70px rgba(0,0,0,.5),0 0 0 1px rgba(67,217,255,.05)!important}
body.arch-ui-v2 .card{padding:22px 24px!important;margin-bottom:18px!important;overflow:hidden!important}
body.arch-ui-v2 .card::before{background:linear-gradient(90deg,transparent,rgba(67,217,255,.55),rgba(155,123,255,.35),transparent)!important}
body.arch-ui-v2 .card::after{width:2px!important;background:linear-gradient(180deg,transparent,var(--arch-cyan),transparent)!important;opacity:.55!important}
body.arch-ui-v2 .card-head{margin-bottom:18px!important}
body.arch-ui-v2 .card-title{font-size:18px!important;letter-spacing:.02em!important}
body.arch-ui-v2 .card-sub,body.arch-ui-v2 .help,body.arch-ui-v2 .hint{color:var(--arch-muted)!important;line-height:1.75!important}
body.arch-ui-v2 .btn,
body.arch-ui-v2 button.btn,
body.arch-ui-v2 .mode-pill,
body.arch-ui-v2 .mc-row .btn{
  border-radius:13px!important;border-color:var(--arch-line)!important;
  transition:transform .18s cubic-bezier(.2,.8,.2,1),box-shadow .2s,border-color .2s,background .2s!important;
  position:relative!important;overflow:hidden!important;
}
body.arch-ui-v2 .btn:hover,body.arch-ui-v2 .mc-row .btn:hover{transform:translateY(-2px)!important;box-shadow:0 10px 28px rgba(0,0,0,.32)!important}
body.arch-ui-v2 .btn.p,body.arch-ui-v2 .btn.btn-green,body.arch-ui-v2 .btn.primary{
  background:linear-gradient(135deg,var(--arch-cyan),#1d9bff 55%,var(--arch-violet))!important;border-color:transparent!important;color:#031018!important;font-weight:850!important;
  box-shadow:0 10px 30px rgba(67,217,255,.22)!important;
}
body.arch-ui-v2 .badge,body.arch-ui-v2 .chip{border-radius:999px!important;background:rgba(67,217,255,.08)!important;border:1px solid rgba(67,217,255,.18)!important;color:#bfefff!important}
body.arch-ui-v2 .badge.b-ok,body.arch-ui-v2 .badge.ok{background:rgba(72,240,178,.1)!important;border-color:rgba(72,240,178,.28)!important;color:#a9ffe0!important}
body.arch-ui-v2 .badge.b-bad,body.arch-ui-v2 .badge.bad{background:rgba(255,93,122,.1)!important;border-color:rgba(255,93,122,.28)!important;color:#ffc0cc!important}
body.arch-ui-v2 input,body.arch-ui-v2 select,body.arch-ui-v2 textarea{
  background:rgba(5,9,16,.7)!important;border-color:var(--arch-line)!important;border-radius:13px!important;color:var(--arch-text)!important;
  transition:border-color .2s,box-shadow .2s,background .2s!important;
}
body.arch-ui-v2 input:focus,body.arch-ui-v2 select:focus,body.arch-ui-v2 textarea:focus{border-color:rgba(67,217,255,.58)!important;box-shadow:0 0 0 4px rgba(67,217,255,.08)!important;outline:none!important}
body.arch-ui-v2 .tbl{border-collapse:separate!important;border-spacing:0!important}
body.arch-ui-v2 .tbl th{background:rgba(67,217,255,.06)!important;color:#bfefff!important;border-bottom:1px solid var(--arch-line)!important;font-weight:800!important}
body.arch-ui-v2 .tbl td{border-bottom:1px solid rgba(148,163,184,.08)!important}
body.arch-ui-v2 .tbl tr:hover td{background:rgba(67,217,255,.035)!important}
body.arch-ui-v2 .stat{border-radius:18px!important;padding:18px!important}
body.arch-ui-v2 .stat-val{font-variant-numeric:tabular-nums;letter-spacing:-.03em}
body.arch-ui-v2 .mode-strip{gap:10px!important}
body.arch-ui-v2 .mode-pill{border-radius:15px!important;padding:14px 10px!important}
body.arch-ui-v2 .mode-pill.active{background:linear-gradient(135deg,rgba(67,217,255,.28),rgba(155,123,255,.2))!important;border-color:rgba(67,217,255,.44)!important;box-shadow:0 14px 34px rgba(67,217,255,.16)!important}
body.arch-ui-v2 .main-top{background:rgba(7,11,18,.78)!important;border-bottom-color:var(--arch-line)!important}
body.arch-ui-v2 .status-pill{background:rgba(72,240,178,.08)!important;border-color:rgba(72,240,178,.26)!important;color:#a9ffe0!important}
body.arch-ui-v2 ::-webkit-scrollbar{width:10px;height:10px}
body.arch-ui-v2 ::-webkit-scrollbar-track{background:rgba(255,255,255,.02)}
body.arch-ui-v2 ::-webkit-scrollbar-thumb{background:linear-gradient(180deg,rgba(67,217,255,.36),rgba(155,123,255,.28));border-radius:999px;border:2px solid transparent;background-clip:padding-box}
body.arch-ui-v2 ::-webkit-scrollbar-thumb:hover{background:linear-gradient(180deg,rgba(67,217,255,.55),rgba(155,123,255,.42));background-clip:padding-box}
.arch-reveal{opacity:0;transform:translateY(14px);transition:opacity .65s cubic-bezier(.2,.8,.2,1),transform .65s cubic-bezier(.2,.8,.2,1)}
.arch-reveal.arch-in{opacity:1;transform:none}
.page.arch-page-enter{animation:archPageIn .42s cubic-bezier(.2,.8,.2,1) both}
.arch-ripple{position:absolute;border-radius:50%;pointer-events:none;transform:scale(0);animation:archRipple .65s ease-out;background:rgba(255,255,255,.32)}
@keyframes archPageIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes archRipple{to{transform:scale(4);opacity:0}}
@keyframes archPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.45;transform:scale(.85)}}
@keyframes archOrb{to{filter:hue-rotate(360deg)}}
@media (max-width:900px){
  body.arch-ui-v2{display:block!important;overflow:auto!important}
  body.arch-ui-v2 .sidebar{position:sticky!important;top:0!important;width:100%!important;height:auto!important;border-right:0!important;border-bottom:1px solid var(--arch-line)!important;z-index:70!important}
  body.arch-ui-v2 .sb-brand{display:none!important}
  body.arch-ui-v2 .sb-nav{display:flex!important;flex-direction:row!important;overflow-x:auto!important;overflow-y:hidden!important;gap:8px!important;padding:10px 12px!important;touch-action:pan-x!important;-webkit-overflow-scrolling:touch!important}
  body.arch-ui-v2 .sb-item{flex:0 0 auto!important;min-height:40px!important;padding:9px 13px!important;font-size:12.5px!important}
  body.arch-ui-v2 .main{display:block!important;height:auto!important;overflow:visible!important;width:100%!important;min-width:0!important}
  .arch-topbar{position:sticky;top:0;min-height:58px;padding:0 13px;gap:9px}
  .arch-topbar-status{display:none}
  .arch-topbar-clock{display:none}
  .arch-topbar-brand b{font-size:13px}
  .arch-topbar-brand small{font-size:9px}
  body.arch-ui-v2 .content{padding:14px 10px 80px!important}
  body.arch-ui-v2 .card{padding:17px 15px!important;border-radius:18px!important;margin-bottom:13px!important}
  body.arch-ui-v2 .content table{display:block!important;max-width:100%!important;overflow-x:auto!important;white-space:nowrap!important}
  #mobileSettingsBtn{display:none!important}
}
@media (prefers-reduced-motion:reduce){
  .arch-reveal{opacity:1!important;transform:none!important;transition:none!important}
  .page.arch-page-enter{animation:none!important}
  body.arch-ui-v2 .sb-icon,.arch-orb,.arch-status-dot{animation:none!important}
}

</style>
</head>
<body>

<!-- ============ 侧边栏 ============ -->
<aside class="sidebar">
  <div class="sb-brand">
    <div class="sb-icon" aria-hidden="true" style="border-radius:11px;background:linear-gradient(135deg,#42a5ff,#3ddc97);color:#04111e;font-weight:950;font-size:19px;box-shadow:0 8px 24px rgba(66,165,255,.22)">A</div>
    <div>
      <div style="display:flex;align-items:center;gap:6px">
        <div class="sb-name">ARCH 融合中枢</div>
        <button id="themeBtn" type="button" onclick="toggleTheme()" aria-label="切换日间/夜间模式" title="切换日间/夜间模式" style="border:0;background:transparent;color:var(--muted);font-size:16px;line-height:1;padding:0;cursor:pointer">🌙</button>
      </div>
      <div class="sb-sub"><span id="sbCall">NOCALL</span></div>
    </div>
    
  </div>

  <nav class="sb-nav">
        <div class="sb-item active" data-page="dashboard">
      <span class="ico">📊</span>设备运行状态
    </div>
    <div class="sb-item" data-page="rfmode"><span class="ico">📻</span>射频模式配置</div>
    <div class="sb-item" data-page="dmr">
      <span class="ico">🎙️</span>DMR 模式配置
    </div>
    <div class="sb-item" data-page="ysf">
      <span class="ico">🔊</span>YSF 模式配置
    </div>
    <div class="sb-item" data-page="p25">
      <span class="ico">📡</span>P25 模式配置
    </div>
    <div class="sb-item" data-page="nxdn">
      <span class="ico">📶</span>NXDN 模式配置
    </div>
    <div class="sb-item" data-page="dstar">
      <span class="ico">⭐</span>D-STAR 反射器
    </div>
    <div class="sb-item" data-page="fmo"><span class="ico">📡</span>FM 联网配置</div>
    <div class="sb-item" data-page="pocsag">
      <span class="ico">📟</span>POCSAG 寻呼
    </div>
    <div class="sb-item" data-page="ports">
      <span class="ico">🔌</span>端口监听矩阵
    </div>
    <div class="sb-item" data-page="calls">
      <span class="ico">📞</span>通话记录
    </div>
    <div class="sb-item" data-page="db">
      <span class="ico">🗄️</span>数据库更新
    </div>
    <div class="sb-item" data-page="sys">
      <span class="ico">⚙️</span>系统设置
    </div>
  </nav>
</aside>

<!-- ============ 主区 ============ -->
<main class="main">
<header class="arch-topbar" id="archTopbar">
  <div class="arch-topbar-brand"><span class="arch-orb"></span><div><b>ARCH</b><small>融合中枢</small></div></div>
  <div class="arch-topbar-status"><span class="arch-status-dot"></span><span id="archModeLabel">设备控制台</span></div>
  <div class="arch-topbar-spacer"></div>
  <div class="arch-topbar-clock" id="archClock">--:--:--</div>
  <button class="arch-topbar-btn" type="button" onclick="switchPage('sys')" title="系统设置" aria-label="系统设置">⚙️</button>
</header>
  <button id="mobileSettingsBtn" type="button" onclick="switchPage('sys')" aria-label="设置" title="设置">⚙️</button>
  <div class="content">

    <!-- ============ 状态总览 ============ -->
    <section class="page" id="page-dashboard">
      <div class="card" id="freqModeCard">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M3 12h3l3-8 3 16 3-8h6" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
              </svg>
            </div>
            <div>
              <div class="card-title">频率模式列表</div>
            </div>
          </div>
          
        </div>
        <div id="mcMatrix" class="mc-matrix">
          <div class="mc-empty">正在读取多信道状态...</div>
        </div>
      </div>

      <div class="stat-grid">
        <div class="stat">
          <div class="stat-ico r">🌡️</div>
          <div>
            <div class="stat-lbl">CPU 核心温度</div>
            <div class="stat-val" id="stCpu">--</div>
          </div>
        </div>
        <div class="stat">
          <div class="stat-ico b">🧠</div>
          <div>
            <div class="stat-lbl">内存占用</div>
            <div class="stat-val" id="stMem">--</div>
          </div>
        </div>
        <div class="stat">
          <div class="stat-ico a">⏱️</div>
          <div>
            <div class="stat-lbl">系统运行时间</div>
            <div class="stat-val" id="stUptime">--</div>
          </div>
        </div>
        <div class="stat">
          <div class="stat-ico g">📻</div>
          <div>
            <div class="stat-lbl">SX1255 射频板</div>
            <div class="stat-val" id="stSx">--</div>
          </div>
        </div>
        <div class="stat">
          <div class="stat-ico y">⚡</div>
          <div>
            <div class="stat-lbl">CPU 实时占用</div>
            <div class="stat-val" id="stCpuUse">--</div>
          </div>
        </div>
      </div>

      



<div class="card" id="qsoPanel">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M3 12h3l3-8 3 16 3-8h6" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
              </svg>
            </div>
            <div>
              <div class="card-title">通联列表</div>
            </div>
          </div>
          <span class="card-badge">实时解析</span>
        </div>
        <div class="qso-list" id="qsoList">
          <div class="qso-empty"><span class="dot"></span>当前信道守候中...</div>
        </div>

      </div>
    </section>

    <!-- ============ RF ============ -->
    <!-- （频率设置已并入 /multi 的「信道规划」，此页已删除） -->
    <section class="page proto-page" id="page-freq-old" style="display:none;visibility:hidden;height:0;overflow:hidden">
      
    </section>

    <section class="page proto-page" id="page-rf" style="display:none">
<div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M3 12h3l3-8 3 16 3-8h6" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
              </svg>
            </div>
            <div>
              <div class="card-title">频率与信道规划（统一设置）</div>
              <div class="card-sub">本机是<b>多信道同时收发</b>：先定<b>中心频率与通道间距</b>，再为每路通道选择协议、
                必要时单独改该通道的频率。保存后会写入 MMDVM-Multi.ini 与各通道 MMDVM-Host-N.ini，
                并只重启受影响的通道 —— 其它协议页不再需要（也不应该）单独设频率。</div>
            </div>
          </div>
          <span class="card-badge">唯一来源</span>
        </div>

        <div id="freqNow" class="freq-now">
          <div class="mc-empty">正在读取当前各通道频率...</div>
        </div>

        <div class="form-grid" style="margin-top:14px">
          <div class="fld"><label>通道数量 (1-6)   <!-- CH_LIMIT_FIX --></label>
            <input id="fCh" type="number" min="1" max="6" value="1" inputmode="numeric">
            <div class="hint">减少通道会自动停掉并禁用多余的通道实例</div></div>
          <div class="fld"><label>接收中心频率 (MHz)</label>
            <input id="fRx" type="text" inputmode="decimal" value="434.450" oninput="freqHint()">
            <div class="hint" id="fRxMhz">= 434450000 Hz</div></div>
          <div class="fld"><label>发射中心频率 (MHz)</label>
            <input id="fTx" type="text" inputmode="decimal" value="439.450" oninput="freqHint()">
            <div class="hint" id="fTxMhz">= 439450000 Hz</div></div>
          <div class="fld"><label>通道间距 (kHz)</label>
            <input id="fGap" type="text" inputmode="decimal" value="25" oninput="freqHint()">
            <div class="hint">25 = 25 kHz 标准网格；12.5 = 窄带</div></div>
        </div>

        <div class="freq-rows" id="fRows">
          <div class="mc-empty">正在读取通道规划...</div>
        </div>

        <div class="save-row">
          <button class="save-btn rf" onclick="freqSave(false)">✓ 保存并应用（写入配置 + 重启受影响通道）</button>
          <button class="refresh-btn" onclick="freqSave(true)">仅保存规划</button>
          <button class="refresh-btn" onclick="freqLoad()">↻ 重新读取</button>
        </div>
        <div class="hint" style="margin-top:10px">
          说明：每个通道的 RX/TX 留空或点「按间距算」= 使用公式值；直接填写则使用你填的具体频率。
          多信道同时工作时，各通道频率<b>不能重叠</b>（间距需 ≥ 12.5 kHz）。
        </div>
      </div>
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico">📻</div>
            <div>
              <div class="card-title">全局射频载波与基带参数 (RF &amp; Station)</div>
              <div class="card-sub">MMDVM-Host 与 SX1255 物理射频基带集中设置：对 DMR / YSF / P25 / NXDN / D-STAR 等全部模式统一生效</div>
            </div>
          </div>
          <span class="card-badge">SX1255 · DUPLEX</span>
        </div>

        <form class="form-grid" id="form-rf" onsubmit="saveForm(event,'rf')">
          <div class="fld"><label>热点呼号 (Callsign - 全局)</label>
            <input name="callsign" value="" placeholder="NOCALL"><div class="hint">写入 MMDVM-Host.ini [General]，各数字模式通用</div></div>
          <div class="fld full"><div class="hint" style="border:1px dashed var(--border-lit);border-radius:9px;padding:10px 12px;line-height:1.7">
            <b style="color:#fbbf24">频率已统一管理，本页不再单独设置。</b><br>
            本机是<b>多信道同时收发</b>（每路通道可跑不同协议、不同频率），所以频率只在
            左侧「<b>🧩 Multi 多信道配置 → 信道规划</b>」里设置：每路通道一行，填协议 + RX + TX，
            点「应用规划」后自动写入 MMDVM-Multi.ini 与各通道 MMDVM-Host-N.ini。
            本页只负责<b>呼号、发射功率、基带增益</b>。
            <div style="margin-top:9px">
              <button type="button" class="btn btn-green btn-sm" onclick="switchPage('rfmode')">前往「Multi 多信道配置 → 信道规划」设置 →</button>
            </div>
          </div></div>
          <input type="hidden" name="duplex" value="1">
          <div class="fld"><label>工作模式（固定）</label>
            <input value="全双工热点 (Duplex Hotspot) — 本系统固定双工运行" readonly
                   style="background:#0b1420;color:#7d93b2;cursor:not-allowed"></div>
          <div class="fld"><label>射频发射功率档位 (SX1255)</label>
            <select name="power" id="rfPower" onchange="rfPowerChanged()">
              <option value="7">大功率 — 约 +7 dBm / 5 mW（远距离、抗干扰）</option>
              <option value="0">小功率 — 0 dB / 0 dBm / 1 mW（近距离、更健康）</option>
            </select>
            <div class="hint">切换档位会自动调整下面的 TxGain（大功率=30dB / 小功率=10dB），也可再手动微调</div></div>
          <div class="fld"><label>SX1255 硬件基带接收增益 (RxGain 0-60 dB)</label>
            <input name="rxgain" type="number" value="50"><div class="hint">写入 MMDVM-IQ.ini [Soapy] 推荐 50dB</div></div>
          <div class="fld"><label>SX1255 硬件基带发射增益 (TxGain 0-40 dB)</label>
            <input name="txgain" id="rfTxGain" type="number" value="30"><div class="hint">写入 MMDVM-IQ.ini [Soapy]：大功率推荐 30dB / 小功率推荐 10dB（切档位会自动填）</div></div>
          <div class="fld full"><label>台站信息描述 (Description)</label>
            <input name="description" value="ARCH Duplex Hotspot"><div class="hint">仅作记录显示；后端 save-rf 暂不接受该参数（要改请编辑 MMDVM-Host.ini 的 [Info] Description）</div><div class="hint">DMR 身份识别码 (Radio ID) 已移到 <b>DMR 模式</b>页面 —— 它只对 DMR 有效</div></div>
        </form>
        <div class="save-row">
          <button type="submit" form="form-rf" class="save-btn rf">✓ 保存射频参数并更新 MMDVM-Host.ini &amp; MMDVM-IQ.ini</button>
          <button class="refresh-btn" onclick="restartSvc('mmdvmhost')">↻ 重启射频基带</button>
        </div>
      </div>
    </section>

    <!-- ============ DMR ============ -->
    <section class="page proto-page" id="page-dmr" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:var(--green-dim);color:var(--green2)">📻</div>
            <div>
              <div class="card-title">DMR / BrandMeister 网关</div>
              <div class="card-sub">MMDVM 标准配置：呼号 · DMR ID · 色码 · Master 服务器 · 密码</div>
            </div>
          </div>
          <span class="card-badge">DMRGateway</span>
        </div>

        <div class="freq-banner">
          <div class="freq-banner-ico">📡</div>
          <div class="freq-banner-txt">
            <b>当前射频载波频率 <span style="color:var(--green2);font-size:11.5px;margin-left:6px">全模式共享</span></b>
            <span>RX: <b class="dyn-rx">--</b> MHz ｜ TX: <b class="dyn-tx">--</b> MHz ｜ 差频: <b class="dyn-off">--</b> MHz</span>
            <span style="margin-top:5px;color:var(--muted2);font-size:11.5px">手台侧：TX <b class="dyn-rx">--</b> MHz ｜ RX <b class="dyn-tx">--</b> MHz ｜ 色码按本页设置，时隙 1/2 均启用</span>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="switchPage('rfmode')">⚙ 修改射频频率 →</button>
        </div>

        <!-- ===== 唯一状态区（原来有 3 组重复状态，这里合并成一处）===== -->
        <div class="hint" id="dmrStatusBox" style="margin:6px 0 14px;padding:12px;border:1px solid rgba(16,185,129,.35);border-radius:10px">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <span class="badge" id="dmrStatusBadge" style="margin:0">检测中</span>
            <b id="dmrStatusTitle">正在读取 DMR 网关状态…</b>
            <span style="margin-left:auto;display:flex;gap:8px">
              <button type="button" class="btn btn-ghost btn-sm" onclick="dmrRefreshStatus()">↻ 刷新状态</button>
              <button type="button" class="btn btn-sm" onclick="dmrReconnect()">⟳ 重载网关</button>
            </span>
          </div>
          <div id="dmrStatusDetail" style="margin-top:8px;font-size:12px;color:var(--dim);font-family:var(--mono);word-break:break-all"></div>
          <div style="margin-top:6px;font-size:11.5px;color:var(--muted2)">
            接收不通时先核对：① 本页色码与手台一致 ② 手台 TX 频率 = 上方 RX ③ 手台 DMR ID / 时隙与本地一致。
          </div>
        </div>

        <div class="preset-box" style="padding:0;border:none;background:none">
          <div class="sp" id="sp-dmr">
            <div class="sp-head">
              <span class="sp-title">🌐 BrandMeister 服务器选择</span>
              <span class="sp-hint" id="spDmrSrc">数据源：ARCH 镜像（每 4 小时同步）</span>
            </div>
            <div class="sp-bar">
              <span class="sp-ico">🔎</span>
              <input class="sp-in" id="spDmrIn" autocomplete="off" spellcheck="false"
                     placeholder="搜索 Master：4602 / China / 日本 / 韩国 …">
              <span class="sp-cnt" id="spDmrCnt">加载中…</span>
            </div>
            <div class="sp-chips" id="spDmrChips"></div>
            <div class="sp-list" id="spDmrList"><div class="sp-empty">正在读取主机库…</div></div>
            <div class="sp-sel">
              <span>当前选择：</span><b id="spDmrSel">（未选择）</b>
              <button type="button" class="sp-go" onclick="spLoadCurrent('dmr')">↺ 载入当前配置</button>
            </div>
          </div>
        </div>

        <form class="form-grid" id="form-dmr" onsubmit="saveForm(event,'dmr')">
          <div class="fld"><label>电台呼号 Callsign</label>
            <input name="callsign" value="" placeholder="NOCALL" autocomplete="off">
            <div class="essid-note">写入 MMDVM-Host 与 DMRGateway 的 <b>Callsign</b>；BM 会校验呼号与 ID 是否匹配。</div>
          </div>
          <div class="fld"><label>DMR ID（7 位）</label>
            <input name="id" value="" inputmode="numeric" autocomplete="off" placeholder="7 位数字，例如 4600111">
            <div class="essid-note">写入 MMDVM-Host <b>[General] Id</b> 与 DMRGateway <b>Id</b>。</div>
          </div>
          <div class="fld"><label>ESSID 扩展（可选）</label>
            <div style="display:flex;align-items:center;gap:10px">
              <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13.5px;color:#9fb6d6">
                <input type="checkbox" id="essidOn" style="accent-color:#10b981;width:15px;height:15px">启用 ESSID
              </label>
              <input name="essid" id="essidVal" value="01" placeholder="01" maxlength="2" inputmode="numeric" autocomplete="off" style="width:90px">
            </div>
            <div class="essid-note">启用后最终 ID = 7 位 DMR ID + 2 位 ESSID（01–99）。多数热点不需要，默认关闭。</div>
          </div>
          <div class="fld"><label>色码 ColorCode（0–15）</label>
            <input name="cc" type="number" value="7" min="0" max="15">
            <div class="essid-note">必须与手台色码一致，否则收不到手台的发射。</div>
          </div>
          <div class="fld"><label>Master 主机地址</label>
            <input name="bm_host" id="dmrBmHost" value="" autocomplete="off" spellcheck="false" placeholder="从上方选择器点选，或填域名 / IP">
          </div>
          <div class="fld"><label>Master 端口</label>
            <input name="bm_port" type="number" value="62031">
            <div class="essid-note">BrandMeister 标准端口 <b>62031</b>。</div>
          </div>
          <div class="fld full"><label>热点连接密码（BrandMeister Hotspot Security 里设置的那个）</label>
            <input name="bm_pw" type="text" autocomplete="off" spellcheck="false" placeholder="留空＝保持现有密码不变（推荐）">
            <div class="essid-note">留空不会清空原密码；只有确实要改密码时才填写。<b>不是</b> BM 网站登录密码。</div>
          </div>
          <div class="fld full" id="dmrExtraWrap" style="display:none">
            <label>其他 DMR 射频通道的服务器</label>
            <div id="dmrChannelServers"></div>
            <div class="essid-note">通道 2 起使用独立 DMRGateway 实例和独立端口（62041/62042、62051/62052…），可分别连接不同 Master。</div>
          </div>
        </form>
        <div class="save-row">
          <button type="submit" form="form-dmr" class="save-btn dmr">✓ 保存 DMR 参数并重载网关</button>
          <button type="button" class="refresh-btn" onclick="spRefresh('dmr')">↻ 重新载入服务器列表</button>
          <button type="button" class="refresh-btn" onclick="updateDb('dmrhosts')">↻ 更新 DMR 服务器列表</button>
        </div>
      </div>
    </section>

    <!-- ============ YSF ============ -->
    <section class="page proto-page" id="page-ysf" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(6,182,212,.12);color:#22d3ee">🔊</div>
            <div>
              <div class="card-title">YSF 模式配置 (Yaesu System Fusion / C4FM)</div>
              <div class="card-sub">WIRES-X 房间互联、指令穿透与 FT-70D 大写兼容支持</div>
            </div>
          </div>
          <span class="card-badge" style="background:rgba(6,182,212,.12);color:#22d3ee;border-color:rgba(6,182,212,.4)">YSFGateway</span>
        </div>
        <div class="freq-banner">
          <div class="freq-banner-ico" style="background:rgba(6,182,212,.12);color:#22d3ee">📡</div>
          <div class="freq-banner-txt">
            <b>当前热点物理射频载波频率 <span style="color:#22d3ee;font-size:11.5px;margin-left:6px">全模式共享</span></b>
            <span>RX: <b class="dyn-rx">--</b> MHz ｜ TX: <b class="dyn-tx">--</b> MHz ｜ 差频: <b class="dyn-off">--</b> MHz</span>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="switchPage('rfmode')">⚙ 修改射频频率 →</button>
        </div>
        <div class="preset-box" style="padding:0;border:none;background:none">
          <div class="sp" id="sp-ysf">
            <div class="sp-head">
              <span class="sp-title">🌐 YSF / WIRES-X 房间选择器</span>
              <span class="sp-hint" id="spYsfSrc">数据源：Pi-Star YSFHosts</span>
            </div>
            <div class="sp-bar">
              <span class="sp-ico">🔎</span>
              <input class="sp-in" id="spYsfIn" autocomplete="off" spellcheck="false"
                     placeholder="搜索房间：编号 / 国家 / 名称，例如 46087 / CN / China">
              <span class="sp-cnt" id="spYsfCnt">加载中…</span>
            </div>
            <div class="sp-chips" id="spYsfChips"></div>
            <div class="sp-list" id="spYsfList"><div class="sp-empty">正在读取房间列表…</div></div>
            <div class="sp-sel">
              <span>当前选择：</span><b id="spYsfSel">（未选择）</b>
              <button type="button" class="sp-go" onclick="spLoadCurrent('ysf')">↺ 载入当前配置</button>
            </div>
          </div>
        </div>
        <div class="hint" id="ysfGwStatus" style="margin:10px 0 12px;padding:10px 12px;border:1px solid rgba(6,182,212,.35);border-radius:8px">
          <span class="badge" id="ysfGwBadge" style="margin-right:10px">检测中</span>
          <span id="ysfGwServer">服务器/房间：--</span>
        </div>
        <form class="form-grid" id="form-ysf" onsubmit="saveForm(event,'ysf')" style="margin-top:14px">
          <input type="hidden" name="suffix" value="RPT">
          <div class="fld"><label>呼号 (Callsign)</label><input name="callsign" value="" placeholder="NOCALL"></div>
          <div class="fld"><label>启动房间 (Startup Room)</label>
            <input name="startup" id="ysfStartup" value="" placeholder="用上方选择器点选，或手填房间名称（如 CN-YSF-TG46020）；留空=不自动连接">
            <div class="hint">本机 YSFGateway 按 <b>反射器名称</b> 连接（官方示例：<code>Startup=Alabama-Link</code>），所以要填 <b>名称</b>、不是房间号。上方选择器可按 编号 / 国家 / 名称 搜索 1400+ 个 Pi-Star YSF 房间，点选后自动填入名称。</div></div>
          <div class="fld"><label>WIRES-X 指令穿透 (Command Passthrough)</label>
            <select name="wiresx">
              <option value="1">启用（允许手台用 WIRES-X 自行切换房间）</option>
              <option value="0">禁用（固定锁定启动房间）</option>
            </select>
            <div class="hint">启用后可用手台 WIRES-X 搜索并切换房间</div></div>
          <div class="fld"><label>UPPERCASE Hostfiles — 八重洲 FT-70D 兼容</label>
            <select name="uppermode">
              <option value="1">启用（WiresXMakeUpper=1）</option>
              <option value="0">禁用（WiresXMakeUpper=0）</option>
            </select>
            <div class="hint">FT-70D 的 WIRES-X 搜索<b>只识别大写字母</b>。房间名含小写字母时
              FT-70D 搜不到、连不上。启用后网关会把房间名统一转为大写，
              使 FT-70D 能正常搜索并连接房间。</div></div>
        </form>
        <div class="save-row">
          <button type="submit" form="form-ysf" class="save-btn ysf">✓ 保存并应用 YSF 配置</button>
          <button class="refresh-btn" onclick="updateDb('ysfhosts')">↻ 更新 YSF 房间列表</button>
        </div>
      </div>
    </section>

    <!-- ============ P25 ============ -->
    <section class="page proto-page" id="page-p25" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(139,92,246,.12);color:#a78bfa">📡</div>
            <div>
              <div class="card-title">P25 (APCO Phase 1) 数字参数</div>
              <div class="card-sub">手台设置：TX=本页 RX，RX=本页 TX；NAC 293；TG 46002。支持 Pi-Star P25Hosts 全球呼叫组预设切换</div>
            </div>
          </div>
          <span class="card-badge" style="background:rgba(139,92,246,.12);color:#a78bfa;border-color:rgba(139,92,246,.4)">P25Gateway</span>
        </div>
        <div class="freq-banner">
          <div class="freq-banner-ico" style="background:rgba(139,92,246,.12);color:#a78bfa">📡</div>
          <div class="freq-banner-txt">
            <b>当前热点物理射频载波频率 <span style="color:#a78bfa;font-size:11.5px;margin-left:6px">全模式共享</span></b>
            <span>RX: <b class="dyn-rx">--</b> MHz ｜ TX: <b class="dyn-tx">--</b> MHz ｜ 差频: <b class="dyn-off">--</b> MHz</span>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="switchPage('rfmode')">⚙ 修改射频频率 →</button>
        </div>
        <div class="hint" id="p25GwStatus" style="margin:10px 0 12px;padding:10px 12px;border:1px solid rgba(139,92,246,.35);border-radius:8px">
          <span class="badge" id="p25GwBadge" style="margin-right:10px">检测中</span>
          <span id="p25GwServer">服务器/反射器：--</span>
        </div>
        <div class="preset-box" style="border-color:rgba(139,92,246,.3)">
          <div class="preset-box-lbl" style="color:#a78bfa">🌐 P25 呼叫组预设（点选即写入下方「静态通话组」）</div>
          <select id="p25Preset" onchange="applyStaticPreset('p25', this.value)">
            <option value="">— 请选择预设呼叫组 (TG) —</option>
            <option value="10460">P25-10460（CN China Group 中国呼叫组 / TG 10460）</option>
            <option value="10100">P25-10100（Asia Pacific 亚太区 / TG 10100）</option>
            <option value="10200">P25-10200（Europe 欧洲区 / TG 10200）</option>
          </select>
          <div class="hint" id="p25HostHint">主机库加载后会自动补充 Pi-Star P25Hosts 预设</div>
        </div>
        <form class="form-grid" id="form-p25" onsubmit="saveForm(event,'p25')">
          <div class="fld"><label>呼号 (Callsign)</label><input name="callsign" value="" placeholder="NOCALL"></div>
          <div class="fld"><label>NAC 访问码 (默认 293 / $293)</label><input name="nac" value="293"></div>
          <div class="fld full"><label>静态通话组列表 (Static TGs)</label><input id="p25Static" name="static" value="46002"></div>
        </form>
        <div class="save-row">
          <button type="submit" form="form-p25" class="save-btn p25">✓ 保存并应用 P25 配置</button>
          <button class="refresh-btn" onclick="updateDb('p25hosts')">↻ 更新 P25 主机列表</button>
        </div>
      </div>
    </section>

    <!-- ============ NXDN ============ -->
    <section class="page proto-page" id="page-nxdn" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(236,72,153,.12);color:#f472b6">📶</div>
            <div>
              <div class="card-title">NXDN (IDAS / Nexedge) 数字参数</div>
              <div class="card-sub">支持 Pi-Star NXDNHosts 全球通话组预设选择</div>
            </div>
          </div>
          <span class="card-badge" style="background:rgba(236,72,153,.12);color:#f472b6;border-color:rgba(236,72,153,.4)">NXDNGateway</span>
        </div>
        <div class="freq-banner">
          <div class="freq-banner-ico" style="background:rgba(236,72,153,.12);color:#f472b6">📡</div>
          <div class="freq-banner-txt">
            <b>当前热点物理射频载波频率 <span style="color:#f472b6;font-size:11.5px;margin-left:6px">全模式共享</span></b>
            <span>RX: <b class="dyn-rx">--</b> MHz ｜ TX: <b class="dyn-tx">--</b> MHz ｜ 差频: <b class="dyn-off">--</b> MHz</span>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="switchPage('rfmode')">⚙ 修改射频频率 →</button>
        </div>
        <div class="hint" id="nxdnGwStatus" style="margin:10px 0 12px;padding:10px 12px;border:1px solid rgba(236,72,153,.35);border-radius:8px">
          <span class="badge" id="nxdnGwBadge" style="margin-right:10px">检测中</span>
          <span id="nxdnGwServer">服务器/反射器：--</span>
        </div>
        <div class="preset-box" style="border-color:rgba(236,72,153,.3)">
          <div class="preset-box-lbl" style="color:#f472b6">🌐 NXDN 通话组预设（点选即写入下方「静态通话组」）</div>
          <select id="nxdnPreset" onchange="applyStaticPreset('nxdn', this.value)">
            <option value="">— 请选择预设通话组 (TG) —</option>
            <option value="10460">NXDN-10460（CN China National 核心互联组）</option>
            <option value="65000">NXDN-65000（World-Wide 全球通用）</option>
          </select>
          <div class="hint" id="nxdnHostHint">主机库加载后会自动补充 Pi-Star NXDNHosts 预设</div>
        </div>
        <form class="form-grid" id="form-nxdn" onsubmit="saveForm(event,'nxdn')">
          <div class="fld"><label>呼号 (Callsign)</label><input name="callsign" value="" placeholder="NOCALL"></div>
          <div class="fld"><label>NXDN 用户 ID</label><input name="id" type="number" value="" placeholder="例如 46164"></div>
          <div class="fld"><label>RAN 访问码 (1-63)</label><input name="ran" type="number" value="1" min="1" max="63"></div>
          <div class="fld"><label>协议格式 (Protocol)</label>
            <select name="protocol"><option value="Icom">Icom (IDAS 协议)</option><option value="Kenwood">Kenwood (Nexedge 协议)</option></select></div>
          <div class="fld"><label>静态通话组 (Static TGs)</label><input id="nxdnStatic" name="static" value="10460, 65000"></div>
        </form>
        <div class="save-row">
          <button type="submit" form="form-nxdn" class="save-btn nxdn">✓ 保存并应用 NXDN 配置</button>
          <button class="refresh-btn" onclick="updateDb('nxdnhosts')">↻ 更新 NXDN 主机列表</button>
        </div>
      </div>
    </section>

    <!-- ============ D-STAR ============ -->
    <section class="page proto-page" id="page-dstar" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(249,115,22,.12);color:#fb923c">⭐</div>
            <div>
              <div class="card-title">D-STAR / ircDDBGateway 参数</div>
              <div class="card-sub">支持 Pi-Star 全球 REF / XRF / DCS 反射器选择</div>
            </div>
          </div>
          <span class="card-badge" style="background:rgba(249,115,22,.12);color:#fb923c;border-color:rgba(249,115,22,.4)">ircddbgatewayd</span>
        </div>
        <div class="freq-banner">
          <div class="freq-banner-ico" style="background:rgba(249,115,22,.12);color:#fb923c">📡</div>
          <div class="freq-banner-txt">
            <b>当前热点物理射频载波频率 <span style="color:#fb923c;font-size:11.5px;margin-left:6px">全模式共享</span></b>
            <span>RX: <b class="dyn-rx">--</b> MHz ｜ TX: <b class="dyn-tx">--</b> MHz ｜ 差频: <b class="dyn-off">--</b> MHz</span>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="switchPage('rfmode')">⚙ 修改射频频率 →</button>
        </div>
        <div class="preset-box" style="padding:0;border:none;background:none">
          <div class="sp" id="sp-dstar">
            <div class="sp-head">
              <span class="sp-title">⭐ D-STAR 反射器选择器</span>
              <span class="sp-hint" id="spDstarSrc">数据源：Pi-Star DPlus / DExtra / DCS / CCS / XLX / FCS</span>
            </div>
            <div class="sp-bar">
              <span class="sp-ico">🔎</span>
              <input class="sp-in" id="spDstarIn" autocomplete="off" spellcheck="false"
                     placeholder="搜索反射器：REF / XRF / DCS / CCS / XLX / FCS，或输入编号、国家名">
              <span class="sp-cnt" id="spDstarCnt">加载中…</span>
            </div>
            <div class="sp-chips" id="spDstarChips"></div>
            <div class="sp-list" id="spDstarList"><div class="sp-empty">正在读取反射器库…</div></div>
            <div class="sp-mod">
              <label>模块</label>
              <select id="spDstarMod">
                <option value="A" selected>A（ARCH 多模融合，推荐）</option><option value="B">B</option>
                <option value="C">C</option><option value="D">D</option>
                <option value="E">E</option><option value="F">F</option>
                <option value="G">G</option><option value="H">H</option>
                <option value="I">I</option><option value="J">J</option>
                <option value="K">K</option><option value="L">L</option>
                <option value="M">M</option><option value="N">N</option>
                <option value="O">O</option><option value="P">P</option>
                <option value="Q">Q</option><option value="R">R</option>
                <option value="S">S</option><option value="T">T</option>
                <option value="U">U</option><option value="V">V</option>
                <option value="W">W</option><option value="X">X</option>
                <option value="Y">Y</option><option value="Z">Z</option>
              </select>
              <label>开机</label>
              <select id="spDstarStartup">
                <option value="1" selected>自动连接 (Startup)</option>
                <option value="0">手动连接 (Manual)</option>
              </select>
              <span class="sp-hint">模块 A-Z 对应反射器房间（XLX 最多 26 个）。ARCH 的 46002 多模融合挂在 <b>XLXHLJ 模块 A</b>；选好反射器和模块后点保存，网关会自动重连。</span>
            </div>
            <div class="sp-sel">
              <span>当前选择：</span><b id="spDstarSel">（未选择）</b>
              <button type="button" class="sp-go" onclick="spLoadCurrent('dstar')">↺ 载入当前配置</button>
            </div>
          </div>
        </div>
        <div class="preset-box" style="border-color:rgba(249,115,22,.3);margin-top:10px">
          <div class="preset-box-lbl" style="color:#fb923c">🌐 ircDDB 网关路由服务器 (ircDDB Server Host)</div>
          <select id="dstarIrcHost">
            <option value="rr.ircddb.net">rr.ircddb.net（ircDDB 官方）</option>
            <option value="rr.opendv.net">rr.opendv.net（OpenDV）</option>
            <option value="rr.aprs2.net">rr.aprs2.net（备用）</option>
          </select>
          <div class="hint">呼叫路由（ircddbEnabled=1）时使用的服务器，写入
            <b>/etc/ircddbgateway</b> 的 <b>ircddbHostname</b>。</div>
        </div>
        <div class="hint" id="dstarGwStatus" style="margin:10px 0 12px;padding:10px 12px;border:1px solid rgba(249,115,22,.35);border-radius:8px">
          <span class="badge" id="dstarGwBadge" style="margin-right:10px">检测中</span>
          <span id="dstarGwServer">服务器/反射器：--</span>
        </div>
        <form class="form-grid" id="form-dstar" onsubmit="saveForm(event,'dstar')" style="margin-top:14px">
          <input type="hidden" name="reflector1" id="dstar_reflector" value="">
          <input type="hidden" name="dstar_module" id="dstar_module" value="B">
          <input type="hidden" name="atStartup1" id="dstar_atstartup" value="1">
          <div class="fld"><label>D-STAR 本机呼号</label><input name="gw_call" value="" placeholder="留空自动使用本机呼号"></div>
          <input type="hidden" name="rep_call" value="">
          <input type="hidden" name="irc_user" value="">
          <div class="fld"><label>ircDDB 认证密码（可留空）</label><input name="irc_pw" type="text" autocomplete="off" placeholder="可留空"></div>

          <div class="fld full" style="grid-column:1/-1;margin-top:6px">
            <div style="border-top:1px solid #1e2b44;padding-top:12px">
              <div style="font-size:13.5px;font-weight:650;color:#fb923c;margin-bottom:10px">
                ⚙ D-STAR 网关开关（ircDDBGateway）
              </div>
              <div class="form-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px">

                <div class="fld"><label>呼叫路由 (Callsign Routing)</label>
                  <select name="callsign_routing" id="dsRouting">
                    <option value="1">启用 —— 接入 ircDDB 网络，支持呼叫路由</option>
                    <option value="0">关闭 —— 不使用 ircDDB 呼叫路由</option>
                  </select>
                  <div class="hint">启用后写入 ircDDBGateway 的 <b>ircddbEnabled=1</b>：
                    其他电台可直接用你的呼号呼叫你（Callsign Routing），
                    而不需要知道你当前挂在哪个反射器上。需要上方 ircDDB 用户名/密码正确。</div>
                </div>

                <div class="fld"><label>时间通告 (Time Announcement)</label>
                  <select name="time_announce" id="dsTime">
                    <option value="1">启用 —— 语音播报当前时间</option>
                    <option value="0">关闭</option>
                  </select>
                  <div class="hint">启用后由 <b>timeserver</b> 定时在 D-STAR 上语音播报日期与时间。
                    实际开关状态记录在 <b>/etc/timeserver.dissable</b> 标记文件（存在=关闭），
                    播报的语言与间隔写入 <b>/etc/timeserver</b>。</div>
                </div>

                <div class="fld"><label>XRF 使用 D-Plus 协议 (Use D-Plus for XRF)</label>
                  <select name="use_dplus_xrf" id="dsXrf">
                    <option value="0">关闭 —— XRF 走原生 DExtra 协议</option>
                    <option value="1">启用 —— XRF 主机走 D-Plus 协议</option>
                  </select>
                  <div class="hint">部分网络（如 XLX 反射器）的 XRF 端口只接受 D-Plus 协议。
                    启用后写入 <b>/etc/hostfiles.nodextra</b> 标记文件，
                    让 XRF 主机列表使用 D-Plus 方式解析与连接。</div>
                </div>

              </div>
            </div>
          </div>
        </form>
        <div class="save-row">
          <button type="submit" form="form-dstar" class="save-btn dstar">✓ 保存并应用 D-STAR 配置</button>
          <button class="refresh-btn" onclick="spRefresh('dstar')">↻ 重新载入反射器列表</button>
          <button class="refresh-btn" onclick="rebuildHosts()">⟳ 从 Pi-Star 重建主机库</button>
        </div>
      </div>
    </section>

    <!-- ============ FM ============ -->
    <section class="page proto-page" id="page-fm" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico">🎛️</div>
            <div>
              <div class="card-title">FM 模拟双工异频参数</div>
              <div class="card-sub">传统模拟 FM 热点，支持 CTCSS 亚音</div>
            </div>
          </div>
          <span class="card-badge">Analog Duplex</span>
        </div>
        <div class="freq-banner">
          <div class="freq-banner-ico">📡</div>
          <div class="freq-banner-txt">
            <b>当前热点物理射频载波频率 <span style="color:var(--green2);font-size:11.5px;margin-left:6px">全模式共享</span></b>
            <span>RX: <b class="dyn-rx">--</b> MHz ｜ TX: <b class="dyn-tx">--</b> MHz ｜ 差频: <b class="dyn-off">--</b> MHz</span>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="switchPage('rfmode')">⚙ 修改射频频率 →</button>
        </div>
        <form class="form-grid" id="form-fm" onsubmit="saveForm(event,'fm')">
          <div class="fld"><label>呼号 (Callsign)</label><input name="callsign" value="" placeholder="NOCALL"></div>
          <div class="fld"><label>CTCSS 亚音频 (Hz)</label><input name="ctcss" value="88.5"></div>
        </form>
        <div class="save-row">
          <button type="submit" form="form-fm" class="save-btn rf">✓ 保存并应用 FM 配置</button>
        </div>
      </div>
    </section>

    <!-- ============ POCSAG ============ -->
    <section class="page proto-page" id="page-pocsag" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(245,158,11,.12);color:#fbbf24">📟</div>
            <div>
              <div class="card-title">POCSAG 数字寻呼与 DAPNET 接入</div>
              <div class="card-sub">支持无线电爱好者去中心化寻呼网络 DAPNET 网关</div>
            </div>
          </div>
          <span class="card-badge" style="background:rgba(245,158,11,.12);color:#fbbf24;border-color:rgba(245,158,11,.4)">Pager Tx</span>
        </div>
        <div class="preset-box" style="border-color:rgba(245,158,11,.3)">
          <div class="preset-box-lbl" style="color:#fbbf24">🌐 DAPNET 服务器地址 (DAPNET Server)</div>
          <select>
            <option value="dapnet.afu.rwth-aachen.de">dapnet.afu.rwth-aachen.de</option>
            <option value="dapnet.proxy.cn">dapnet.proxy.cn</option>
          </select>
        </div>
        <form class="form-grid" id="form-pocsag" onsubmit="saveForm(event,'pocsag')" style="margin-top:14px">
          <div class="fld"><label>寻呼发射频率 (MHz)</label>
            <input id="pocMhz" type="text" inputmode="decimal" value="439.500" oninput="pocFreqTyped()">
            <input type="hidden" name="freq" id="pocFreq" value="439500000">
            <div class="hint" id="pocMhzHz">= 439500000 Hz（按习惯填 MHz：439.500 / 439.5125）</div></div>
          <div class="fld"><label>DAPNET 鉴权密钥</label><input type="password" placeholder="请输入热点连接密码，非 BM 网站登录密码"></div>
        </form>
        <div class="save-row">
          <button type="submit" form="form-pocsag" class="save-btn pocsag">✓ 保存并应用 POCSAG 配置</button>
        </div>
      </div>
    </section>

    <!-- ============ Multi 多信道配置（内嵌 /multi 看板） ============ -->
    <!-- ============ 射频模式配置（独立页面内嵌） ============ -->

    <!-- ============ 端口矩阵 ============ -->

    <section class="page proto-page" id="page-ports" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico">🔌</div>
            <div>
              <div class="card-title">UDP 端口拓扑与套接字进程矩阵</div>
              <div class="card-sub">实时 ss -lunp 端口占用探针</div>
            </div>
          </div>
          <span class="card-badge">自愈校验 OK</span>
        </div>
        <div id="portList" style="margin-top:8px">
          <div class="qso-empty">正在扫描端口...</div>
        </div>
      </div>
    </section>

    <!-- ============ 数据库 ============ -->
    <section class="page proto-page" id="page-db" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico">🗄️</div>
            <div>
              <div class="card-title">服务器与呼号数据库更新中心</div>
              <div class="card-sub">一键同步全球最新 Master 主节点、YSF 房间列表与 RadioID.net 呼号库</div>
            </div>
          </div>
          <span class="card-badge">Sync Ready</span>
        </div>
        <div class="db-cards">
          <div class="db-card">
            <h3>🌐 更新网关主机与房间列表 <span class="hdr-tag blue">每日自动</span></h3>
            <p>拉取最新的 DMR_Hosts、YSFHosts、P25Hosts 与 NXDNHosts，收录全球 2,900+ 节点。</p>
            <button class="btn btn-blue" style="width:100%" onclick="updateDb('hosts')">
              ⬇ 立即更新网关服务器列表
            </button>
          </div>
          <div class="db-card">
            <h3>🌍 更新全球呼号与 ID 数据库 <span class="hdr-tag green">RadioID.net</span></h3>
            <p>下载全球业余无线电电台数据 (DMRIds.dat / user.csv)，在热点通话时直接屏幕解析呼号、姓名与所属国籍。</p>
            <button class="btn btn-green" style="width:100%" onclick="updateDb('dmrid')">
              ⟳ 立即更新全球 ID 数据库
            </button>
          </div>
        </div>
        <div style="margin-top:18px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <div style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:800;color:var(--green2)">
              &gt;_ 更新任务实时输出控制台
            </div>
            <span style="font-size:13px;color:var(--dim);font-family:var(--mono)">/usr/local/bin/update-database.sh</span>
          </div>
          <div class="console" id="dbConsole"><span class="tag-in">[系统启动]</span> 本地主机与 ID 数据库下载本地完成
当前网关主机版本: 2026.09.08-rev4（收录 540+ BM 主节点，1,840+ YSF 房间，320+ P25 组）
当前全球 RadioID 数据库: 285,412 条台站呼号映射记录（RadioID.net 同步）</div>
        </div>
      </div>
    </section>

    <!-- ============ 系统设置 ============ -->
    <section class="page proto-page" id="page-sys" style="display:none">
      <div class="card" id="remoteDebugCard" style="display:none">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(56,189,248,.12);color:#38bdf8">🛰️</div>
            <div>
              <div class="card-title">远程调测（维护通道）</div>
              <div class="card-sub">默认开启。开启后厂家可在你授权下远程诊断/修复本机（root 权限，仅用于排障）</div>
            </div>
          </div>
          <span class="card-badge" id="remoteDebugBadge">读取中</span>
        </div>
        <div class="hint" style="margin:0 0 12px">
          通道只走<b>设备主动出站</b>（无需公网 IP、不开任何入站端口、不是 VPN）。
          关闭后设备不再接受任何远程命令。
        </div>
        <div class="save-row" style="margin-top:0">
          <button type="button" class="btn btn-green" onclick="remoteDebugCtl('on')">✓ 开启远程调测</button>
          <button type="button" class="btn" onclick="remoteDebugCtl('off')">✕ 关闭远程调测</button>
          <button type="button" class="refresh-btn" onclick="remoteDebugStatus()">↻ 刷新状态</button>
          <span class="mc-tip" id="remoteDebugMsg"></span>
        </div>
      </div>
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico">⚙️</div>
            <div>
              <div class="card-title">系统设置</div>
              
            </div>
          </div>
          <span class="card-badge">SYSTEM</span>
        </div>
        <div class="form-grid">
          <div class="fld full"><label>系统信息</label>
            <div class="hint" id="sysInfo" style="line-height:1.9">读取中…</div>
            <div id="updateNotice" style="display:none;margin-top:8px;padding:10px 12px;border:1px solid #f59e0b;border-radius:8px;background:rgba(245,158,11,.12);color:#fde68a;line-height:1.6"></div></div>
          <div class="fld full"><label>网络状态</label>
            <div class="hint" id="sysNet" style="line-height:1.9">读取中…</div>
            <div style="margin-top:8px"><button class="btn" onclick="sysLoad()">↻ 刷新信息</button></div></div>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(34,211,238,.12);color:#22d3ee">📶</div>
            <div>
              <div class="card-title">WiFi 无线网络</div>
              
            </div>
          </div>
          <span class="card-badge" id="wifiBackend">WIFI</span>
        </div>
        <div class="hint" id="wifiStatus" style="line-height:1.9">读取中…</div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:12px 0">
          <button class="btn btn-green" onclick="wifiScan()">🔎 扫描附近 WiFi</button>
          <button class="btn" onclick="wifiSaved()">📋 已保存的网络</button>
          <button class="btn" onclick="wifiStatusShow()">↻ 刷新状态</button>
          <button class="btn" id="wifiRadioBtn" onclick="wifiRadioToggle()">📴 关闭 WiFi</button>
          <span class="hint" id="wifiMsg"></span>
        </div>
        <div id="wifiList" class="hint" style="line-height:2;max-height:300px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:8px">点「扫描附近 WiFi」列出热点（含 2.4G/5G、信号、加密方式）；点「用它」自动填入 SSID</div>
        <div class="form-grid" style="margin-top:14px">
          <div class="fld"><label>SSID（WiFi 名称，支持中文）</label>
            <input id="wifiSsid" placeholder="例如 MLCIA 4"></div>
          <div class="fld"><label>密码（留空 = 开放网络）</label>
            <input id="wifiPw" type="password" placeholder="WPA / WPA2 / WPA3 密码">
            <div class="hint">连接成功后写入系统，重启自动重连</div></div>
          <div class="fld"><label>附加选项</label>
            <label style="display:flex;gap:7px;align-items:center;font-size:13px;color:var(--muted)">
              <input type="checkbox" id="wifiHidden"> 隐藏网络（不广播 SSID，需手动输入）
            </label></div>
          <div class="fld full">
            <button class="btn btn-green" onclick="wifiConnect()">✓ 连接此 WiFi</button>
            <button class="btn" style="margin-left:8px" onclick="wifiDisconnect()">断开当前 WiFi</button>
            <span class="hint" id="wifiConnMsg"></span>
          </div>
        </div>
        <div id="wifiSavedBox" style="margin-top:14px"></div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(96,165,250,.12);color:#60a5fa">🛠️</div>
            <div>
              <div class="card-title">网络参数（IP / 掩码 / 网关 / DNS）</div>
              <div class="card-sub">有线与无线都支持，改完立即生效；若网段变化，本页面会断开，请用新地址重新访问</div>
            </div>
          </div>
          <span class="card-badge">LAN / WLAN</span>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
          <span class="hint">网卡</span>
          <select id="netIface" onchange="netParamsLoad()" style="min-width:180px">
            <option value="eth0">eth0（有线网口）</option>
            <option value="wlan0">wlan0（WiFi 无线）</option>
          </select>
          <span class="hint">获取方式</span>
          <select id="netMode" style="min-width:150px">
            <option value="static">静态地址</option>
            <option value="dhcp">自动获取 (DHCP)</option>
          </select>
          <button class="btn" onclick="netParamsLoad()">↻ 读取当前配置</button>
          <span class="hint" id="netMsg"></span>
        </div>
        <div class="form-grid">
          <div class="fld"><label>IP 地址</label><input id="netIp" placeholder="192.168.7.134"></div>
          <div class="fld"><label>子网掩码 / 前缀</label><input id="netMask" placeholder="255.255.255.0 或 24"></div>
          <div class="fld"><label>网关</label><input id="netGw" placeholder="192.168.7.1"></div>
          <div class="fld"><label>DNS（多个用逗号分隔）</label><input id="netDns" placeholder="192.168.7.1,1.1.1.1"></div>
          <div class="fld full">
            <button class="btn btn-green" onclick="netApply()">✓ 应用网络参数</button>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(239,68,68,.12);color:#f87171">🔌</div>
            <div>
              <div class="card-title">系统操作</div>
              <div class="card-sub">热点服务与电源控制</div>
            </div>
          </div>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <button class="btn" onclick="sysAction('restart-hotspot')">↻ 重启热点服务</button>
          <button class="btn" onclick="sysAction('restart-web')">↻ 重启 Web 控制台</button>
          <button class="btn btn-blue" onclick="sysAction('reboot')">⏻ 重启设备</button>
          <button class="btn" style="background:#7f1d1d;color:#fee2e2" onclick="sysAction('shutdown')">⏻ 关机</button>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(239,68,68,.12);color:#f87171">&#9851;</div>
            <div>
              <div class="card-title">恢复出厂设置</div>
              <div class="card-sub">清除全部用户配置，恢复到初始状态</div>
            </div>
          </div>
          <span class="card-badge" style="background:rgba(239,68,68,.15);color:#fca5a5;border-color:#7f1d1d">危险操作</span>
        </div>
        <div class="hint" style="line-height:1.9">
          <b>将清除：</b>呼号 / DMR ID、信道规划、各模式(DMR/YSF/P25/NXDN/D-STAR)参数与网关登录、FM 哑音与尾音、FMO 证书与注册。<br>
          <b>将保留：</b>固件版本、设备ID、远程管理与调测通道、WiFi 与网络设置（确保仍能进入设备）。<br>
          执行前会把当前配置自动备份到设备 <code>/var/backups/mmdvm/</code>。
        </div>
        <label style="display:flex;align-items:center;gap:8px;margin-top:10px">
          <input type="checkbox" id="frNet" style="width:auto">
          <span class="hint" style="margin:0">同时清除 WiFi / 网络设置（会导致设备断网，仅在本机操作时勾选）</span>
        </label>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px">
          <button class="btn" style="background:#7f1d1d;color:#fee2e2" onclick="factoryReset()">&#9851; 恢复出厂设置</button>
          <span class="hint" id="frMsg"></span>
        </div>
      </div>

            <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(251,191,36,.12);color:#fbbf24">⬆️</div>
            <div><div class="card-title">树莓派系统更新</div><div class="card-sub">更新树莓派系统软件包</div></div>
          </div>
          <span class="card-badge">SYSTEM</span>
        </div>
        <div class="hint" id="aptStat" style="line-height:1.9">点击“树莓派系统更新”检查并升级系统软件包。</div><div id="sysProgWrap" style="display:none;height:10px;background:rgba(148,163,184,.22);border-radius:999px;overflow:hidden;margin-top:10px"><div id="sysProgBar" style="height:100%;width:0%;background:#60a5fa;transition:width .4s ease"></div></div><div class="hint" id="sysProgText" style="display:none;margin-top:6px"></div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px">
          <button class="btn btn-green" onclick="hotspotOnlineUpgrade()">⬆️ 树莓派系统更新</button>
          <span class="hint" id="hotspotMsg"></span>
        </div>
      </div>
<div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(168,85,247,.12);color:#c084fc">🧩</div>
            <div><div class="card-title">ARCH固件更新</div><div class="card-sub">在线更新 ARCH 热点固件</div></div>
          </div>
          <span class="card-badge">FIRMWARE</span>
        </div>
        <div class="hint" id="fwStat" style="line-height:1.9">点击“ARCH固件更新”在线检查并升级 ARCH 热点固件。</div><div id="fwProgWrap" style="display:none;height:10px;background:rgba(148,163,184,.22);border-radius:999px;overflow:hidden;margin-top:10px"><div id="fwProgBar" style="height:100%;width:0%;background:#60a5fa;transition:width .4s ease"></div></div><div class="hint" id="fwProgText" style="display:none;margin-top:6px"></div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px">
          <button class="btn btn-green" onclick="fwOnlineUpgrade()">⬆️ ARCH固件更新</button>
          <div class="hint" style="width:100%;margin-top:8px">升级期间网页会短暂 502 / 变慢（Web 服务在重启），属正常现象；等日志出现“安装完成”再刷新即可。</div>
          <span class="hint" id="fwOnlineMsg"></span>
        </div>
      </div>
<div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(96,165,250,.12);color:#60a5fa">🕒</div>
            <div>
              <div class="card-title">时间与同步</div>
              
            </div>
          </div>
          <span class="card-badge">TIME</span>
        </div>
        <div class="hint" id="sysTime" style="line-height:1.9">读取中…</div>
        <div class="form-grid" style="margin-top:14px">
          <div class="fld"><label>时区</label>
            <input id="sysTzInput" list="sysTzList" placeholder="Asia/Shanghai">
            <datalist id="sysTzList">
              <option value="Asia/Shanghai"></option>
              <option value="Asia/Hong_Kong"></option>
              <option value="Asia/Taipei"></option>
              <option value="Asia/Tokyo"></option>
              <option value="Asia/Singapore"></option>
              <option value="UTC"></option>
            </datalist>
            <div class="hint">中国大陆用 Asia/Shanghai</div></div>
          <div class="fld"><label>NTP 自动校时</label>
            <div style="display:flex;gap:10px;flex-wrap:wrap">
              <button class="btn btn-green" onclick="sysNtp(true)">开启</button>
              <button class="btn" onclick="sysNtp(false)">关闭</button>
            </div></div>
          <div class="fld full">
            <button class="btn btn-blue" onclick="sysSetTz()">✓ 设置时区</button>
            <button class="btn" style="margin-left:8px" onclick="sysTimeSync()">⟳ 立即同步时间</button>
            <span class="hint" id="sysTimeMsg"></span>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(245,158,11,.12);color:#fbbf24">⏰</div>
            <div>
              <div class="card-title">定时重启</div>
              
            </div>
          </div>
        </div>
        <div class="hint" id="sysSched" style="line-height:1.9">读取中…</div>
        <div class="form-grid" style="margin-top:14px">
          <div class="fld"><label>频率</label>
            <select id="sysSchedMode">
              <option value="daily">每天</option>
              <option value="weekly">每周一</option>
            </select></div>
          <div class="fld"><label>时刻（HH:MM，24 小时制）</label>
            <input id="sysSchedTime" value="04:00" placeholder="04:00"></div>
          <div class="fld full">
            <button class="btn btn-green" onclick="sysSchedSave()">✓ 保存并开启</button>
            <button class="btn" style="margin-left:8px;background:#7f1d1d;color:#fee2e2" onclick="sysSchedOff()">关闭定时重启</button>
            <span class="hint" id="sysSchedMsg"></span>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(148,163,184,.12);color:#94a3b8">🧰</div>
            <div>
              <div class="card-title">维护与诊断</div>
              <div class="card-sub">配置备份、服务日志、网络连通性测试</div>
            </div>
          </div>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
          <button class="btn" onclick="sysBackup()">💾 备份配置</button>
          <select id="sysLogSvc" style="min-width:160px">
            <option value="mmdvm-multi">mmdvm-multi</option>
            <option value="mmdvmhost@1">mmdvmhost@1</option>
            <option value="mmdvmhost@2">mmdvmhost@2</option>
            <option value="mmdvmhost@3">mmdvmhost@3</option>
            <option value="mmdvmhost@4">mmdvmhost@4</option>
            <option value="mmdvmhost@5">mmdvmhost@5</option>
            <option value="mmdvm-web">mmdvm-web</option>
          </select>
          <input id="sysLogN" value="60" style="width:70px" title="行数">
          <button class="btn" onclick="sysLogs()">📜 查看日志</button>
          <input id="sysPingHost" value="223.5.5.5" style="width:130px" title="要 ping 的主机">
          <button class="btn" onclick="sysPing()">🌐 ping 测试</button>
          <button class="btn" onclick="sysOutClear()">清空输出</button>
        </div>
        <pre id="sysOut" style="display:none;margin-top:12px;padding:12px;background:rgba(0,0,0,.28);border:1px solid var(--border);border-radius:8px;max-height:340px;overflow:auto;white-space:pre-wrap;font-size:13px;line-height:1.6;color:var(--muted)"></pre>
      </div>
      <div class="card" id="modeSwitchCard" style="padding:12px 16px">
        <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
          <div class="h-ico" style="background:rgba(var(--theme-rgb),.12);color:var(--theme-2)">🔁</div>
          <div style="flex:1;min-width:190px">
            <div class="card-title" style="font-size:15px">运行模式</div>
            <div class="hint" style="margin-top:3px" id="modeHint">常规模式：DMR / D-STAR / YSF / P25 / NXDN / FM 六个通道同时工作</div>
          </div>
          <button class="btn" onclick="modeSwitch('mmdvm')">切到常规模式</button>
        </div>
      </div>
      <div class="card" style="padding:12px 16px;margin-bottom:0">
        <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
          <div class="h-ico" style="background:rgba(var(--theme-rgb),.12);color:var(--theme-2)">🌓</div>
          <div style="flex:1;min-width:150px">
            <div class="card-title" style="font-size:15px">显示模式</div>
            <div class="hint" style="margin-top:3px" id="themeModeHint">当前：夜间模式（深色）</div>
          </div>
          <button class="btn" id="themeModeBtn" onclick="themeModeToggle()">☀️ 切换到日间模式</button>
        </div>
      </div>
    </section>

    <!-- ============ 通话记录 ============ -->
    <section class="page proto-page" id="page-calls" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(16,185,129,.12);color:#34d399">📞</div>
            <div>
              <div class="card-title">通话记录</div>
              
            </div>
          </div>
          <span class="card-badge">CALL LOG</span>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
          <button class="btn btn-green" onclick="callsLoad()">↻ 立即刷新</button>
          <button class="btn" id="callsAutoBtn" onclick="callsToggleAuto()">⏱ 自动刷新：开</button>
          <span class="hint" id="callsMsg"></span>
        </div>
      </div>
      <div id="callsBox"><div class="card"><div class="qso-empty">正在读取通话记录…</div></div></div>
    </section>

    <!-- ===== FM 联网配置（内联页，左侧导航保持） ===== -->
    <section class="page proto-page" id="page-fmo" style="display:none">
      <div class="hint" id="fmGwStatus" style="margin:0 0 10px;padding:10px 12px;border:1px solid rgba(16,185,129,.35);border-radius:8px">
        <span class="badge" id="fmGwBadge" style="margin-right:10px">检测中</span>
        <span id="fmGwServer">FMO 服务器：--</span>
      </div>
      <iframe id="fmoFrame" data-src="/fmo?embed=1" title="FM 联网配置" style="width:100%;height:calc(100vh - 166px);min-height:672px;border:0;border-radius:14px;background:transparent"></iframe>
    </section>


    <!-- ===== 射频模式配置（内联页，含引擎/服务/日志诊断卡） ===== -->
<section class="page proto-page" id="page-rfmode" style="display:none">

      <!-- ===== ② 台站与射频参数 ===== -->
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:var(--green-dim);color:var(--green2)">&#128295;</div>
            <div>
              <div class="card-title">台站与射频参数（呼号 &middot; 功率 &middot; 增益）</div>
              <div class="card-sub">热点呼号写入 MMDVM-Host 与各网关；功率/增益写入 MMDVM-IQ.ini [Soapy]，全模式通用</div>
            </div>
          </div>
          <span class="card-badge">SX1255</span>
        </div>

        <div class="form-grid">
          <div class="fld"><label>本机呼号 (Callsign)</label>
            <input id="rfmCall" placeholder="NOCALL" maxlength="10" autocomplete="off">
            <div class="hint">一定要与 BrandMeister / 网关注册的呼号一致（会写进 MMDVM-Host 与各网关）</div></div>
          <div class="fld"><label>射频发射功率档位 (SX1255)</label>
            <select id="rfmPower">
              <option value="7">大功率 &#8212; 7 级 / +7 dBm / 5 mW（远距离、抗干扰）</option>
              <option value="0">小功率 &#8212; 0 级 / 0 dBm / 1 mW（近距离、更健康）</option>
            </select>
            <div class="hint">切换档位会自动把 TxGain 设为 30dB（大功率）或 10dB（小功率）</div></div>
          <div class="fld"><label>SX1255 硬件基带接收增益 (RxGain 0-60 dB)</label>
            <input id="rfmRxGain" type="number" min="0" max="60" value="50">
            <div class="hint">MMDVM-IQ.ini [Soapy]，推荐 50 dB（此值为当前配置，已自动读取）</div></div>
          <div class="fld"><label>SX1255 硬件基带发射增益 (TxGain 0-40 dB)</label>
            <input id="rfmTxGain" type="number" min="0" max="40" value="30">
            <div class="hint">大功率推荐 30dB / 小功率推荐 10dB</div></div>
        </div>

        <div class="mc-foot" style="margin:12px 0 0">
          <button class="btn btn-green btn-sm" onclick="rfmSaveStation()">&#128190; 保存呼号 / 功率 / 增益</button>
          <span class="hint" id="rfmStMsg" style="margin-left:10px"></span>
        </div>
      </div>

      <!-- ===== ③ 信道规划 ===== -->
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:var(--amber-dim);color:var(--amber2)">&#128226;</div>
            <div>
              <div class="card-title">信道规划（多信道同时收发）</div>
              <div class="card-sub">先定通道数量与中心频率，再给每路选协议；频率可直接填 MHz —— 系统自动换算成 Hz 并写入 MMDVM-Multi.ini 与各通道配置</div>
            </div>
          </div>
          <span class="card-badge">统一频率架构</span>
        </div>

        <div class="freq-banner">
          <div class="freq-banner-ico">&#128225;</div>
          <div class="freq-banner-txt">
            <b>当前各通道实际频率</b>
            <span id="rfmBanner">读取中&hellip;</span>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="rfmPlanLoad()">&#8635; 重新读取</button>
        </div>

        <div class="form-grid" style="margin-top:12px">
          <div class="fld"><label>通道数量 (1 - 6)</label>
            <select id="rfmNCh" onchange="rfmNChChanged()"></select>
            <div class="hint">调小 = 停用多余通道（<b>配置会保留</b>）；以后调回大数字再保存即可恢复多通道<br><b style="color:#fbbf24">采样率随通道数自动切换：1-5 路 → 150 kHz（省 CPU）；6-11 路 → 300 kHz</b></div></div>
          <div class="fld"><label>接收中心频率 (MHz)</label>
            <input id="rfmCrx" type="text" inputmode="decimal" placeholder="434.450" oninput="rfmCenterTyped()">
            <div class="hint" id="rfmCrxHz">例：434.450</div></div>
          <div class="fld"><label>发射中心频率 (MHz)</label>
            <input id="rfmCtx" type="text" inputmode="decimal" placeholder="439.450" oninput="rfmCenterTyped()">
            <div class="hint" id="rfmCtxHz">例：439.450</div></div>
          <div class="fld"><label>通道间距 (kHz)</label>
            <select id="rfmGap" onchange="rfmPlanFreqs()">
              <option value="25">25 kHz（标准，6 路以内用这个）</option>
              <option value="12.5">12.5 kHz（窄带）</option>
            </select>
            <div class="hint">SX1255 各通道按 25 kHz 网格排列，中心频率决定整体位置</div></div>
        </div>

        <div id="rfmRows" style="margin-top:12px"><div class="sp-empty">正在读取通道规划&hellip;</div></div>

        <div class="mc-foot" style="margin:12px 0 0">
          <button class="btn btn-green btn-sm" onclick="rfmPlanApply(false)">&#9889; 保存并应用（写入配置 + 重启受影响通道）</button>
          <button class="btn btn-ghost btn-sm" onclick="rfmPlanApply(true)">仅保存规划</button>
          <span class="hint" id="rfmPlanMsg" style="margin-left:10px"></span>
        </div>
        <div class="hint" style="margin-top:9px;line-height:1.75">
          <b style="color:#fbbf24">频率输入习惯：</b>直接按 MHz 填 ——
          25 kHz 步进写 <b>439.500</b>（= 439500000 Hz），12.5 kHz 步进写 <b>439.5125</b>（= 439512500 Hz）。<br>
          改任意一路的频率时，会以「它」为准反推中心频率，其它通道按硬件网格跟着走；
          表格右侧显示的 MHz 就是设备真实收发频率，对讲机请照它设置。
        </div>
      </div>

      <!-- ===== ③ 各通道状态与切换 ===== -->
      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(56,189,248,.12);color:#38bdf8">&#128251;</div>
            <div>
              <div class="card-title">各通道状态与模式切换</div>
              <div class="card-sub">列出全部 1 - 7 路（含未启用），可随时切模式 / 重启；未启用的通道把「通道数量」调大后保存即可恢复</div>
            </div>
          </div>
          <span class="card-badge">ARCH 融合中枢</span>
        </div>

        <div id="rfmChans"><div class="sp-empty">正在读取通道&hellip;</div></div>

        <div class="mc-foot" style="margin:12px 0 0">
          <button class="btn btn-ghost btn-sm" onclick="rfmLoad()">&#8635; 刷新状态</button>
          <button class="btn btn-ghost btn-sm" onclick="rfmRestartAll()">&#8634; 全部重启</button>
          <span class="hint" id="rfmMsg" style="margin-left:10px"></span>
        </div>
      </div>


    </section>











    <!-- ============ 页脚 ============ -->
    <footer class="footer">
      <div class="title"><a class="title-link" href="https://arch.hljrsa.cn/" target="_blank" rel="noopener noreferrer">ARCH 业余无线电融合中枢</a></div>
      <div>致敬开源项目及作者：MMDVM / MMDVMHost (Jonathan Naylor, G4KLX) · Pi-Star &amp; Gateways (Andy Taylor, MW0MWZ)</div>
      <div>本项目以 GNU GPL v3 许可证发布，欢迎在相同许可证下继续开源。</div>
<div class="author">作者：BG2GA · <a href="https://arch.hljrsa.cn/disclaimer/">免责声明</a></div>
    </footer>

  </div>
</main>

<div id="toast"></div>

<script>
/* ==================== ★ 前端错误上报（远程诊断用）====================
   浏览器里任何 JS 报错都会：① 在页面顶部显示红色错误条；② POST 到
   /api/clientlog，写进设备日志 /tmp/mmdvm-client.log，便于远程定位。
   window.__uiBuild 是界面版本号，用于确认浏览器是否加载了最新页面。 */
window.__uiBuild = 'UI-20260911-2150';
(function(){
  function rep(kind, msg, src, line, col){
    try{
      fetch('/api/clientlog', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({kind:kind, msg:String(msg == null ? '' : msg), src:String(src == null ? '' : src),
                              line:line == null ? '' : line, col:col == null ? '' : col,
                              build:window.__uiBuild, url:location.href,
                              ua:(navigator && navigator.userAgent) || ''})});
    }catch(e){}
    if(kind !== 'error' && kind !== 'promise'){ return; }   // 诊断信息只写日志，不弹红条
    try{
      var b = document.getElementById('jsErrBar');
      if(!b){
        b = document.createElement('div');
        b.id = 'jsErrBar';
        b.style.cssText = 'position:fixed;left:50%;top:50%;transform:translate(-50%,-50%) scale(.96);z-index:99999;background:rgba(13,21,32,.97);border:1px solid #2b3f57;color:#eaf2ff;padding:20px 34px;border-radius:14px;box-shadow:0 24px 64px rgba(0,0,0,.7),0 0 0 1px rgba(255,255,255,.05) inset;font-size:16px;font-weight:600;letter-spacing:.3px;text-align:center;max-width:min(560px,86vw);max-height:40vh;overflow-y:auto;word-break:break-word;white-space:pre-wrap;line-height:1.6;opacity:0;transition:opacity .3s ease,transform .3s cubic-bezier(.2,.9,.3,1.25);display:none'
          + 'padding:8px 12px;font:13px/1.5 Consolas,monospace;white-space:pre-wrap;max-height:40%;overflow:auto';
        (document.body || document.documentElement).appendChild(b);
      }
      b.textContent += '[前端错误 ' + window.__uiBuild + '] ' + kind + ': ' + msg
        + (src ? ('  @ ' + src + ':' + line + ':' + col) : '') + '\n';
    }catch(e){}
  }
  window.__repErr = rep;
  window.addEventListener('error', function(e){ rep('error', e.message, e.filename, e.lineno, e.colno); });
  window.addEventListener('unhandledrejection', function(e){
    var r = e && e.reason;
    rep('promise', (r && (r.message || r)) || 'unknown');
  });
  /* ★ 结构自愈：若某页面区块被错误嵌套在另一个 .page 内（历史补丁遗留的标签不平衡），
     浏览器里自动把它挪回正确的父容器，避免"点开一片空白"。 */
  try{
    var _mainEl = null;
    var _all = document.querySelectorAll('.page');
    if(_all && _all.length){
      for(var _i = 0; _i < _all.length; _i++){
        var _p = _all[_i].parentElement;
        if(_p && _p.classList && _p.classList.contains('page')){
          _mainEl = _mainEl || document.querySelector('main') || document.body;
          _mainEl.appendChild(_all[_i]);
          rep && rep('heal', 'moved ' + _all[_i].id + ' out of ' + _p.id, location.pathname, '', '');
        }
      }
    }
  }catch(e){}
  window.addEventListener('load', function(){
    rep('boot', '页面已加载', location.pathname, '', '');
    try{

    }catch(e){}
  });
})();



// ============ 呼号前缀 → 国家/地区 + 国旗 ============
// 数据来源：ITU 呼号前缀分配表
// 注意：台湾地区前缀（BM-BQ, BU-BX）不列入，按需求跳过
const PREFIX_DB = {
  // --- 中国 ---
  "B":    {flag:"🇨🇳", name:"中国"},
  "XS":   {flag:"🇨🇳", name:"中国"},
  "3H":   {flag:"🇨🇳", name:"中国"}, "3I":   {flag:"🇨🇳", name:"中国"},
  "3J":   {flag:"🇨🇳", name:"中国"}, "3K":   {flag:"🇨🇳", name:"中国"},
  "3L":   {flag:"🇨🇳", name:"中国"}, "3M":   {flag:"🇨🇳", name:"中国"},
  "3N":   {flag:"🇨🇳", name:"中国"}, "3O":   {flag:"🇨🇳", name:"中国"},
  "3P":   {flag:"🇨🇳", name:"中国"}, "3Q":   {flag:"🇨🇳", name:"中国"},
  "3R":   {flag:"🇨🇳", name:"中国"}, "3S":   {flag:"🇨🇳", name:"中国"},
  "3T":   {flag:"🇨🇳", name:"中国"}, "3U":   {flag:"🇨🇳", name:"中国"},
  "VR":   {flag:"🇭🇰", name:"中国香港"},
  "XX":   {flag:"🇲🇴", name:"中国澳门"},
  // --- 亚洲 ---
  "JA":   {flag:"🇯🇵", name:"日本"}, "JB":   {flag:"🇯🇵", name:"日本"},
  "JC":   {flag:"🇯🇵", name:"日本"}, "JD":   {flag:"🇯🇵", name:"日本"},
  "JE":   {flag:"🇯🇵", name:"日本"}, "JF":   {flag:"🇯🇵", name:"日本"},
  "JG":   {flag:"🇯🇵", name:"日本"}, "JH":   {flag:"🇯🇵", name:"日本"},
  "JI":   {flag:"🇯🇵", name:"日本"}, "JJ":   {flag:"🇯🇵", name:"日本"},
  "JK":   {flag:"🇯🇵", name:"日本"}, "JL":   {flag:"🇯🇵", name:"日本"},
  "JM":   {flag:"🇯🇵", name:"日本"}, "JN":   {flag:"🇯🇵", name:"日本"},
  "JO":   {flag:"🇯🇵", name:"日本"}, "JP":   {flag:"🇯🇵", name:"日本"},
  "JQ":   {flag:"🇯🇵", name:"日本"}, "JR":   {flag:"🇯🇵", name:"日本"},
  "JS":   {flag:"🇯🇵", name:"日本"},
  "7J":   {flag:"🇯🇵", name:"日本"}, "7K":   {flag:"🇯🇵", name:"日本"},
  "7L":   {flag:"🇯🇵", name:"日本"}, "7M":   {flag:"🇯🇵", name:"日本"},
  "7N":   {flag:"🇯🇵", name:"日本"},
  "8J":   {flag:"🇯🇵", name:"日本"}, "8K":   {flag:"🇯🇵", name:"日本"},
  "8L":   {flag:"🇯🇵", name:"日本"}, "8M":   {flag:"🇯🇵", name:"日本"},
  "8N":   {flag:"🇯🇵", name:"日本"},
  "HL":   {flag:"🇰🇷", name:"韩国"}, "HM":   {flag:"🇰🇷", name:"韩国"},
  "DS":   {flag:"🇰🇷", name:"韩国"}, "DT":   {flag:"🇰🇷", name:"韩国"},
  "D7":   {flag:"🇰🇷", name:"韩国"}, "D8":   {flag:"🇰🇷", name:"韩国"},
  "D9":   {flag:"🇰🇷", name:"韩国"},
  "6K":   {flag:"🇰🇷", name:"韩国"}, "6L":   {flag:"🇰🇷", name:"韩国"},
  "6M":   {flag:"🇰🇷", name:"韩国"}, "6N":   {flag:"🇰🇷", name:"韩国"},
  "VU":   {flag:"🇮🇳", name:"印度"},
  "AT":   {flag:"🇮🇳", name:"印度"}, "AU":   {flag:"🇮🇳", name:"印度"},
  "AV":   {flag:"🇮🇳", name:"印度"}, "AW":   {flag:"🇮🇳", name:"印度"},
  "VT":   {flag:"🇮🇳", name:"印度"}, "VW":   {flag:"🇮🇳", name:"印度"},
  "8T":   {flag:"🇮🇳", name:"印度"}, "8Y":   {flag:"🇮🇳", name:"印度"},
  "HS":   {flag:"🇹🇭", name:"泰国"}, "E2":   {flag:"🇹🇭", name:"泰国"},
  "YB":   {flag:"🇮🇩", name:"印度尼西亚"}, "YC":   {flag:"🇮🇩", name:"印度尼西亚"},
  "YD":   {flag:"🇮🇩", name:"印度尼西亚"}, "YE":   {flag:"🇮🇩", name:"印度尼西亚"},
  "YF":   {flag:"🇮🇩", name:"印度尼西亚"}, "YG":   {flag:"🇮🇩", name:"印度尼西亚"},
  "YH":   {flag:"🇮🇩", name:"印度尼西亚"},
  "7A":   {flag:"🇮🇩", name:"印度尼西亚"}, "7B":   {flag:"🇮🇩", name:"印度尼西亚"},
  "7C":   {flag:"🇮🇩", name:"印度尼西亚"}, "7D":   {flag:"🇮🇩", name:"印度尼西亚"},
  "7E":   {flag:"🇮🇩", name:"印度尼西亚"}, "7F":   {flag:"🇮🇩", name:"印度尼西亚"},
  "7G":   {flag:"🇮🇩", name:"印度尼西亚"}, "7H":   {flag:"🇮🇩", name:"印度尼西亚"},
  "7I":   {flag:"🇮🇩", name:"印度尼西亚"},
  "8A":   {flag:"🇮🇩", name:"印度尼西亚"}, "8B":   {flag:"🇮🇩", name:"印度尼西亚"},
  "8C":   {flag:"🇮🇩", name:"印度尼西亚"}, "8D":   {flag:"🇮🇩", name:"印度尼西亚"},
  "8E":   {flag:"🇮🇩", name:"印度尼西亚"}, "8F":   {flag:"🇮🇩", name:"印度尼西亚"},
  "8G":   {flag:"🇮🇩", name:"印度尼西亚"}, "8H":   {flag:"🇮🇩", name:"印度尼西亚"},
  "8I":   {flag:"🇮🇩", name:"印度尼西亚"},
  "9M":   {flag:"🇲🇾", name:"马来西亚"}, "9W":   {flag:"🇲🇾", name:"马来西亚"},
  "DU":   {flag:"🇵🇭", name:"菲律宾"}, "DV":   {flag:"🇵🇭", name:"菲律宾"},
  "DW":   {flag:"🇵🇭", name:"菲律宾"}, "DX":   {flag:"🇵🇭", name:"菲律宾"},
  "DY":   {flag:"🇵🇭", name:"菲律宾"}, "DZ":   {flag:"🇵🇭", name:"菲律宾"},
  "4D":   {flag:"🇵🇭", name:"菲律宾"}, "4E":   {flag:"🇵🇭", name:"菲律宾"},
  "4F":   {flag:"🇵🇭", name:"菲律宾"}, "4G":   {flag:"🇵🇭", name:"菲律宾"},
  "4H":   {flag:"🇵🇭", name:"菲律宾"}, "4I":   {flag:"🇵🇭", name:"菲律宾"},
  "XV":   {flag:"🇻🇳", name:"越南"}, "3W":   {flag:"🇻🇳", name:"越南"},
  "9V":   {flag:"🇸🇬", name:"新加坡"},
  "AP":   {flag:"🇵🇰", name:"巴基斯坦"},
  "S2":   {flag:"🇧🇩", name:"孟加拉国"}, "S3":   {flag:"🇧🇩", name:"孟加拉国"},
  "4S":   {flag:"🇱🇰", name:"斯里兰卡"},
  "9N":   {flag:"🇳🇵", name:"尼泊尔"},
  "T6":   {flag:"🇦🇫", name:"阿富汗"}, "YA":   {flag:"🇦🇫", name:"阿富汗"},
  "EP":   {flag:"🇮🇷", name:"伊朗"}, "EQ":   {flag:"🇮🇷", name:"伊朗"},
  "YI":   {flag:"🇮🇶", name:"伊拉克"}, "HN":   {flag:"🇮🇶", name:"伊拉克"},
  "HZ":   {flag:"🇸🇦", name:"沙特阿拉伯"}, "7Z":   {flag:"🇸🇦", name:"沙特阿拉伯"},
  "8Z":   {flag:"🇸🇦", name:"沙特阿拉伯"},
  "A6":   {flag:"🇦🇪", name:"阿联酋"}, "A7":   {flag:"🇶🇦", name:"卡塔尔"},
  "A9":   {flag:"🇧🇭", name:"巴林"}, "A4":   {flag:"🇴🇲", name:"阿曼"},
  "A5":   {flag:"🇧🇹", name:"不丹"},
  "9K":   {flag:"🇰🇼", name:"科威特"},
  "JY":   {flag:"🇯🇴", name:"约旦"},
  "4X":   {flag:"🇮🇱", name:"以色列"}, "4Z":   {flag:"🇮🇱", name:"以色列"},
  "TA":   {flag:"🇹🇷", name:"土耳其"}, "TB":   {flag:"🇹🇷", name:"土耳其"},
  "TC":   {flag:"🇹🇷", name:"土耳其"}, "YM":   {flag:"🇹🇷", name:"土耳其"},
  "EK":   {flag:"🇦🇲", name:"亚美尼亚"},
  "4L":   {flag:"🇬🇪", name:"格鲁吉亚"},
  "4K":   {flag:"🇦🇿", name:"阿塞拜疆"},
  "UN":   {flag:"🇰🇿", name:"哈萨克斯坦"}, "UP":   {flag:"🇰🇿", name:"哈萨克斯坦"},
  "EX":   {flag:"🇰🇬", name:"吉尔吉斯斯坦"},
  "EY":   {flag:"🇹🇯", name:"塔吉克斯坦"},
  "EZ":   {flag:"🇹🇲", name:"土库曼斯坦"},
  "UK":   {flag:"🇺🇿", name:"乌兹别克斯坦"}, "UJ":   {flag:"🇺🇿", name:"乌兹别克斯坦"},
  "UM":   {flag:"🇺🇿", name:"乌兹别克斯坦"},
  "JT":   {flag:"🇲🇳", name:"蒙古"},
  // --- 大洋洲 ---
  "VK":   {flag:"🇦🇺", name:"澳大利亚"}, "AX":   {flag:"🇦🇺", name:"澳大利亚"},
  "VH":   {flag:"🇦🇺", name:"澳大利亚"}, "VI":   {flag:"🇦🇺", name:"澳大利亚"},
  "VJ":   {flag:"🇦🇺", name:"澳大利亚"}, "VL":   {flag:"🇦🇺", name:"澳大利亚"},
  "VM":   {flag:"🇦🇺", name:"澳大利亚"}, "VN":   {flag:"🇦🇺", name:"澳大利亚"},
  "VZ":   {flag:"🇦🇺", name:"澳大利亚"},
  "ZL":   {flag:"🇳🇿", name:"新西兰"},
  "P2":   {flag:"🇵🇬", name:"巴布亚新几内亚"},
  "3D2":  {flag:"🇫🇯", name:"斐济"},
  "A3":   {flag:"🇹🇴", name:"汤加"},
  "KH":   {flag:"🇺🇸", name:"美国"}, "AH":   {flag:"🇺🇸", name:"美国"},
  // --- 欧洲 ---
  "DL":   {flag:"🇩🇪", name:"德国"}, "DA":   {flag:"🇩🇪", name:"德国"},
  "DB":   {flag:"🇩🇪", name:"德国"}, "DC":   {flag:"🇩🇪", name:"德国"},
  "DD":   {flag:"🇩🇪", name:"德国"}, "DF":   {flag:"🇩🇪", name:"德国"},
  "DG":   {flag:"🇩🇪", name:"德国"}, "DH":   {flag:"🇩🇪", name:"德国"},
  "DJ":   {flag:"🇩🇪", name:"德国"}, "DK":   {flag:"🇩🇪", name:"德国"},
  "DM":   {flag:"🇩🇪", name:"德国"}, "DP":   {flag:"🇩🇪", name:"德国"},
  "Y2":   {flag:"🇩🇪", name:"德国"}, "Y3":   {flag:"🇩🇪", name:"德国"},
  "Y4":   {flag:"🇩🇪", name:"德国"}, "Y5":   {flag:"🇩🇪", name:"德国"},
  "Y6":   {flag:"🇩🇪", name:"德国"}, "Y7":   {flag:"🇩🇪", name:"德国"},
  "Y8":   {flag:"🇩🇪", name:"德国"}, "Y9":   {flag:"🇩🇪", name:"德国"},
  "G":    {flag:"🇬🇧", name:"英国"}, "M":    {flag:"🇬🇧", name:"英国"},
  "2":    {flag:"🇬🇧", name:"英国"},
  "VS":   {flag:"🇬🇧", name:"英国"}, "ZB":   {flag:"🇬🇧", name:"英国"},
  "ZC":   {flag:"🇬🇧", name:"英国"}, "ZD":   {flag:"🇬🇧", name:"英国"},
  "ZE":   {flag:"🇬🇧", name:"英国"}, "ZF":   {flag:"🇬🇧", name:"英国"},
  "ZG":   {flag:"🇬🇧", name:"英国"}, "ZH":   {flag:"🇬🇧", name:"英国"},
  "ZI":   {flag:"🇬🇧", name:"英国"}, "ZJ":   {flag:"🇬🇧", name:"英国"},
  "ZN":   {flag:"🇬🇧", name:"英国"}, "ZO":   {flag:"🇬🇧", name:"英国"},
  "ZQ":   {flag:"🇬🇧", name:"英国"},
  "F":    {flag:"🇫🇷", name:"法国"},
  "TM":   {flag:"🇫🇷", name:"法国"}, "TK":   {flag:"🇫🇷", name:"法国"},
  "TO":   {flag:"🇫🇷", name:"法国"}, "TX":   {flag:"🇫🇷", name:"法国"},
  "HW":   {flag:"🇫🇷", name:"法国"},
  "I":    {flag:"🇮🇹", name:"意大利"},
  "IK":   {flag:"🇮🇹", name:"意大利"}, "IZ":   {flag:"🇮🇹", name:"意大利"},
  "IU":   {flag:"🇮🇹", name:"意大利"}, "IW":   {flag:"🇮🇹", name:"意大利"},
  "IN":   {flag:"🇮🇹", name:"意大利"}, "IV":   {flag:"🇮🇹", name:"意大利"},
  "IX":   {flag:"🇮🇹", name:"意大利"},
  "EA":   {flag:"🇪🇸", name:"西班牙"}, "EB":   {flag:"🇪🇸", name:"西班牙"},
  "EC":   {flag:"🇪🇸", name:"西班牙"}, "ED":   {flag:"🇪🇸", name:"西班牙"},
  "EE":   {flag:"🇪🇸", name:"西班牙"}, "EF":   {flag:"🇪🇸", name:"西班牙"},
  "EG":   {flag:"🇪🇸", name:"西班牙"}, "EH":   {flag:"🇪🇸", name:"西班牙"},
  "AM":   {flag:"🇪🇸", name:"西班牙"}, "AN":   {flag:"🇪🇸", name:"西班牙"},
  "AO":   {flag:"🇪🇸", name:"西班牙"},
  "CT":   {flag:"🇵🇹", name:"葡萄牙"}, "CR":   {flag:"🇵🇹", name:"葡萄牙"},
  "CS":   {flag:"🇵🇹", name:"葡萄牙"}, "CQ":   {flag:"🇵🇹", name:"葡萄牙"},
  "CU":   {flag:"🇵🇹", name:"葡萄牙"},
  "PA":   {flag:"🇳🇱", name:"荷兰"}, "PB":   {flag:"🇳🇱", name:"荷兰"},
  "PC":   {flag:"🇳🇱", name:"荷兰"}, "PD":   {flag:"🇳🇱", name:"荷兰"},
  "PE":   {flag:"🇳🇱", name:"荷兰"}, "PF":   {flag:"🇳🇱", name:"荷兰"},
  "PG":   {flag:"🇳🇱", name:"荷兰"}, "PH":   {flag:"🇳🇱", name:"荷兰"},
  "PI":   {flag:"🇳🇱", name:"荷兰"},
  "ON":   {flag:"🇧🇪", name:"比利时"}, "OO":   {flag:"🇧🇪", name:"比利时"},
  "OP":   {flag:"🇧🇪", name:"比利时"}, "OQ":   {flag:"🇧🇪", name:"比利时"},
  "OR":   {flag:"🇧🇪", name:"比利时"}, "OS":   {flag:"🇧🇪", name:"比利时"},
  "OT":   {flag:"🇧🇪", name:"比利时"},
  "HB":   {flag:"🇨🇭", name:"瑞士"}, "HE":   {flag:"🇨🇭", name:"瑞士"},
  "HB0":  {flag:"🇱🇮", name:"列支敦士登"},
  "OE":   {flag:"🇦🇹", name:"奥地利"},
  "SM":   {flag:"🇸🇪", name:"瑞典"}, "SA":   {flag:"🇸🇪", name:"瑞典"},
  "SB":   {flag:"🇸🇪", name:"瑞典"}, "SC":   {flag:"🇸🇪", name:"瑞典"},
  "SD":   {flag:"🇸🇪", name:"瑞典"}, "SE":   {flag:"🇸🇪", name:"瑞典"},
  "SF":   {flag:"🇸🇪", name:"瑞典"}, "SG":   {flag:"🇸🇪", name:"瑞典"},
  "SH":   {flag:"🇸🇪", name:"瑞典"}, "SI":   {flag:"🇸🇪", name:"瑞典"},
  "SJ":   {flag:"🇸🇪", name:"瑞典"}, "SK":   {flag:"🇸🇪", name:"瑞典"},
  "SL":   {flag:"🇸🇪", name:"瑞典"}, "7S":   {flag:"🇸🇪", name:"瑞典"},
  "8S":   {flag:"🇸🇪", name:"瑞典"},
  "LA":   {flag:"🇳🇴", name:"挪威"}, "LB":   {flag:"🇳🇴", name:"挪威"},
  "LC":   {flag:"🇳🇴", name:"挪威"}, "LD":   {flag:"🇳🇴", name:"挪威"},
  "LE":   {flag:"🇳🇴", name:"挪威"}, "LF":   {flag:"🇳🇴", name:"挪威"},
  "LG":   {flag:"🇳🇴", name:"挪威"}, "LH":   {flag:"🇳🇴", name:"挪威"},
  "LI":   {flag:"🇳🇴", name:"挪威"}, "LJ":   {flag:"🇳🇴", name:"挪威"},
  "LK":   {flag:"🇳🇴", name:"挪威"}, "LL":   {flag:"🇳🇴", name:"挪威"},
  "LM":   {flag:"🇳🇴", name:"挪威"}, "LN":   {flag:"🇳🇴", name:"挪威"},
  "OZ":   {flag:"🇩🇰", name:"丹麦"}, "OU":   {flag:"🇩🇰", name:"丹麦"},
  "OV":   {flag:"🇩🇰", name:"丹麦"}, "5P":   {flag:"🇩🇰", name:"丹麦"},
  "5Q":   {flag:"🇩🇰", name:"丹麦"},
  "OH":   {flag:"🇫🇮", name:"芬兰"}, "OF":   {flag:"🇫🇮", name:"芬兰"},
  "OG":   {flag:"🇫🇮", name:"芬兰"}, "OI":   {flag:"🇫🇮", name:"芬兰"},
  "OJ":   {flag:"🇫🇮", name:"芬兰"},
  "TF":   {flag:"🇮🇸", name:"冰岛"},
  "SP":   {flag:"🇵🇱", name:"波兰"}, "SQ":   {flag:"🇵🇱", name:"波兰"},
  "SN":   {flag:"🇵🇱", name:"波兰"}, "SO":   {flag:"🇵🇱", name:"波兰"},
  "SR":   {flag:"🇵🇱", name:"波兰"}, "HF":   {flag:"🇵🇱", name:"波兰"},
  "3Z":   {flag:"🇵🇱", name:"波兰"},
  "OK":   {flag:"🇨🇿", name:"捷克"}, "OL":   {flag:"🇨🇿", name:"捷克"},
  "OM":   {flag:"🇸🇰", name:"斯洛伐克"},
  "HA":   {flag:"🇭🇺", name:"匈牙利"}, "HG":   {flag:"🇭🇺", name:"匈牙利"},
  "YO":   {flag:"🇷🇴", name:"罗马尼亚"}, "YP":   {flag:"🇷🇴", name:"罗马尼亚"},
  "YQ":   {flag:"🇷🇴", name:"罗马尼亚"}, "YR":   {flag:"🇷🇴", name:"罗马尼亚"},
  "LZ":   {flag:"🇧🇬", name:"保加利亚"},
  "SV":   {flag:"🇬🇷", name:"希腊"}, "SW":   {flag:"🇬🇷", name:"希腊"},
  "SX":   {flag:"🇬🇷", name:"希腊"}, "SY":   {flag:"🇬🇷", name:"希腊"},
  "SZ":   {flag:"🇬🇷", name:"希腊"}, "J4":   {flag:"🇬🇷", name:"希腊"},
  "9A":   {flag:"🇭🇷", name:"克罗地亚"},
  "S5":   {flag:"🇸🇮", name:"斯洛文尼亚"},
  "YU":   {flag:"🇷🇸", name:"塞尔维亚"}, "YT":   {flag:"🇷🇸", name:"塞尔维亚"},
  "E7":   {flag:"🇧🇦", name:"波黑"},
  "Z3":   {flag:"🇲🇰", name:"北马其顿"},
  "ZA":   {flag:"🇦🇱", name:"阿尔巴尼亚"},
  "4O":   {flag:"🇲🇪", name:"黑山"},
  "ES":   {flag:"🇪🇪", name:"爱沙尼亚"},
  "YL":   {flag:"🇱🇻", name:"拉脱维亚"},
  "LY":   {flag:"🇱🇹", name:"立陶宛"},
  "EU":   {flag:"🇧🇾", name:"白俄罗斯"}, "EW":   {flag:"🇧🇾", name:"白俄罗斯"},
  "UR":   {flag:"🇺🇦", name:"乌克兰"}, "US":   {flag:"🇺🇦", name:"乌克兰"},
  "UT":   {flag:"🇺🇦", name:"乌克兰"}, "UU":   {flag:"🇺🇦", name:"乌克兰"},
  "UV":   {flag:"🇺🇦", name:"乌克兰"}, "UW":   {flag:"🇺🇦", name:"乌克兰"},
  "UX":   {flag:"🇺🇦", name:"乌克兰"}, "UY":   {flag:"🇺🇦", name:"乌克兰"},
  "UZ":   {flag:"🇺🇦", name:"乌克兰"}, "EM":   {flag:"🇺🇦", name:"乌克兰"},
  "EN":   {flag:"🇺🇦", name:"乌克兰"}, "EO":   {flag:"🇺🇦", name:"乌克兰"},
  "ER":   {flag:"🇲🇩", name:"摩尔多瓦"},
  "R":    {flag:"🇷🇺", name:"俄罗斯"}, "RA":   {flag:"🇷🇺", name:"俄罗斯"},
  "RK":   {flag:"🇷🇺", name:"俄罗斯"}, "RN":   {flag:"🇷🇺", name:"俄罗斯"},
  "RU":   {flag:"🇷🇺", name:"俄罗斯"}, "RV":   {flag:"🇷🇺", name:"俄罗斯"},
  "RW":   {flag:"🇷🇺", name:"俄罗斯"}, "RX":   {flag:"🇷🇺", name:"俄罗斯"},
  "RZ":   {flag:"🇷🇺", name:"俄罗斯"},
  "UA":   {flag:"🇷🇺", name:"俄罗斯"}, "UB":   {flag:"🇷🇺", name:"俄罗斯"},
  "UC":   {flag:"🇷🇺", name:"俄罗斯"}, "UD":   {flag:"🇷🇺", name:"俄罗斯"},
  "UE":   {flag:"🇷🇺", name:"俄罗斯"}, "UF":   {flag:"🇷🇺", name:"俄罗斯"},
  "UG":   {flag:"🇷🇺", name:"俄罗斯"}, "UH":   {flag:"🇷🇺", name:"俄罗斯"},
  "UI":   {flag:"🇷🇺", name:"俄罗斯"},
  // --- 美洲 ---
  "W":    {flag:"🇺🇸", name:"美国"}, "K":    {flag:"🇺🇸", name:"美国"},
  "N":    {flag:"🇺🇸", name:"美国"},
  "AA":   {flag:"🇺🇸", name:"美国"}, "AB":   {flag:"🇺🇸", name:"美国"},
  "AC":   {flag:"🇺🇸", name:"美国"}, "AD":   {flag:"🇺🇸", name:"美国"},
  "AE":   {flag:"🇺🇸", name:"美国"}, "AF":   {flag:"🇺🇸", name:"美国"},
  "AG":   {flag:"🇺🇸", name:"美国"}, "AH":   {flag:"🇺🇸", name:"美国"},
  "AI":   {flag:"🇺🇸", name:"美国"}, "AJ":   {flag:"🇺🇸", name:"美国"},
  "AK":   {flag:"🇺🇸", name:"美国"}, "AL":   {flag:"🇺🇸", name:"美国"},
  "VE":   {flag:"🇨🇦", name:"加拿大"}, "VA":   {flag:"🇨🇦", name:"加拿大"},
  "VO":   {flag:"🇨🇦", name:"加拿大"}, "VY":   {flag:"🇨🇦", name:"加拿大"},
  "VX":   {flag:"🇨🇦", name:"加拿大"},
  "CF":   {flag:"🇨🇦", name:"加拿大"}, "CG":   {flag:"🇨🇦", name:"加拿大"},
  "CH":   {flag:"🇨🇦", name:"加拿大"}, "CI":   {flag:"🇨🇦", name:"加拿大"},
  "CJ":   {flag:"🇨🇦", name:"加拿大"}, "CK":   {flag:"🇨🇦", name:"加拿大"},
  "CY":   {flag:"🇨🇦", name:"加拿大"}, "CZ":   {flag:"🇨🇦", name:"加拿大"},
  "XJ":   {flag:"🇨🇦", name:"加拿大"}, "XK":   {flag:"🇨🇦", name:"加拿大"},
  "XL":   {flag:"🇨🇦", name:"加拿大"}, "XM":   {flag:"🇨🇦", name:"加拿大"},
  "XN":   {flag:"🇨🇦", name:"加拿大"}, "XO":   {flag:"🇨🇦", name:"加拿大"},
  "XE":   {flag:"🇲🇽", name:"墨西哥"}, "XF":   {flag:"🇲🇽", name:"墨西哥"},
  "4A":   {flag:"🇲🇽", name:"墨西哥"}, "4B":   {flag:"🇲🇽", name:"墨西哥"},
  "4C":   {flag:"🇲🇽", name:"墨西哥"},
  "PY":   {flag:"🇧🇷", name:"巴西"}, "PP":   {flag:"🇧🇷", name:"巴西"},
  "PQ":   {flag:"🇧🇷", name:"巴西"}, "PR":   {flag:"🇧🇷", name:"巴西"},
  "PS":   {flag:"🇧🇷", name:"巴西"}, "PT":   {flag:"🇧🇷", name:"巴西"},
  "PU":   {flag:"🇧🇷", name:"巴西"}, "PV":   {flag:"🇧🇷", name:"巴西"},
  "PW":   {flag:"🇧🇷", name:"巴西"}, "PX":   {flag:"🇧🇷", name:"巴西"},
  "ZV":   {flag:"🇧🇷", name:"巴西"}, "ZW":   {flag:"🇧🇷", name:"巴西"},
  "ZX":   {flag:"🇧🇷", name:"巴西"}, "ZY":   {flag:"🇧🇷", name:"巴西"},
  "ZZ":   {flag:"🇧🇷", name:"巴西"},
  "LU":   {flag:"🇦🇷", name:"阿根廷"}, "LW":   {flag:"🇦🇷", name:"阿根廷"},
  "AY":   {flag:"🇦🇷", name:"阿根廷"}, "AZ":   {flag:"🇦🇷", name:"阿根廷"},
  "CE":   {flag:"🇨🇱", name:"智利"}, "CA":   {flag:"🇨🇱", name:"智利"},
  "CB":   {flag:"🇨🇱", name:"智利"}, "CC":   {flag:"🇨🇱", name:"智利"},
  "CD":   {flag:"🇨🇱", name:"智利"}, "XQ":   {flag:"🇨🇱", name:"智利"},
  "XR":   {flag:"🇨🇱", name:"智利"}, "3G":   {flag:"🇨🇱", name:"智利"},
  "OA":   {flag:"🇵🇪", name:"秘鲁"}, "OB":   {flag:"🇵🇪", name:"秘鲁"},
  "OC":   {flag:"🇵🇪", name:"秘鲁"}, "4T":   {flag:"🇵🇪", name:"秘鲁"},
  "HK":   {flag:"🇨🇴", name:"哥伦比亚"}, "HJ":   {flag:"🇨🇴", name:"哥伦比亚"},
  "5J":   {flag:"🇨🇴", name:"哥伦比亚"}, "5K":   {flag:"🇨🇴", name:"哥伦比亚"},
  "YV":   {flag:"🇻🇪", name:"委内瑞拉"}, "YW":   {flag:"🇻🇪", name:"委内瑞拉"},
  "YX":   {flag:"🇻🇪", name:"委内瑞拉"}, "YY":   {flag:"🇻🇪", name:"委内瑞拉"},
  "CX":   {flag:"🇺🇾", name:"乌拉圭"}, "CV":   {flag:"🇺🇾", name:"乌拉圭"},
  "CW":   {flag:"🇺🇾", name:"乌拉圭"},
  "ZP":   {flag:"🇵🇾", name:"巴拉圭"},
  "CP":   {flag:"🇧🇴", name:"玻利维亚"},
  "HC":   {flag:"🇪🇨", name:"厄瓜多尔"}, "HD":   {flag:"🇪🇨", name:"厄瓜多尔"},
  "8R":   {flag:"🇬🇾", name:"圭亚那"},
  "PZ":   {flag:"🇸🇷", name:"苏里南"},
  // --- 非洲 ---
  "ZS":   {flag:"🇿🇦", name:"南非"}, "ZR":   {flag:"🇿🇦", name:"南非"},
  "ZU":   {flag:"🇿🇦", name:"南非"},
  "5Z":   {flag:"🇰🇪", name:"肯尼亚"}, "5Y":   {flag:"🇰🇪", name:"肯尼亚"},
  "5N":   {flag:"🇳🇬", name:"尼日利亚"}, "5O":   {flag:"🇳🇬", name:"尼日利亚"},
  "SU":   {flag:"🇪🇬", name:"埃及"}, "SS":   {flag:"🇪🇬", name:"埃及"},
  "6A":   {flag:"🇪🇬", name:"埃及"}, "6B":   {flag:"🇪🇬", name:"埃及"},
  "CN":   {flag:"🇲🇦", name:"摩洛哥"}, "5C":   {flag:"🇲🇦", name:"摩洛哥"},
  "5D":   {flag:"🇲🇦", name:"摩洛哥"}, "5E":   {flag:"🇲🇦", name:"摩洛哥"},
  "5F":   {flag:"🇲🇦", name:"摩洛哥"}, "5G":   {flag:"🇲🇦", name:"摩洛哥"},
  "3V":   {flag:"🇹🇳", name:"突尼斯"}, "TS":   {flag:"🇹🇳", name:"突尼斯"},
  "7X":   {flag:"🇩🇿", name:"阿尔及利亚"}, "7R":   {flag:"🇩🇿", name:"阿尔及利亚"},
  "7T":   {flag:"🇩🇿", name:"阿尔及利亚"}, "7U":   {flag:"🇩🇿", name:"阿尔及利亚"},
  "7V":   {flag:"🇩🇿", name:"阿尔及利亚"}, "7W":   {flag:"🇩🇿", name:"阿尔及利亚"},
  "7Y":   {flag:"🇩🇿", name:"阿尔及利亚"},
  "5A":   {flag:"🇱🇾", name:"利比亚"},
  "ST":   {flag:"🇸🇩", name:"苏丹"},
  "ET":   {flag:"🇪🇹", name:"埃塞俄比亚"},
  "5X":   {flag:"🇺🇬", name:"乌干达"},
  "5H":   {flag:"🇹🇿", name:"坦桑尼亚"},
  "9J":   {flag:"🇿🇲", name:"赞比亚"},
  "Z2":   {flag:"🇿🇼", name:"津巴布韦"},
  "7Q":   {flag:"🇲🇼", name:"马拉维"},
  "C9":   {flag:"🇲🇿", name:"莫桑比克"},
  "D2":   {flag:"🇦🇴", name:"安哥拉"}, "D3":   {flag:"🇦🇴", name:"安哥拉"},
  "9X":   {flag:"🇷🇼", name:"卢旺达"},
  "9U":   {flag:"🇧🇮", name:"布隆迪"},
  "5R":   {flag:"🇲🇬", name:"马达加斯加"}, "5S":   {flag:"🇲🇬", name:"马达加斯加"},
  "3B":   {flag:"🇲🇺", name:"毛里求斯"},
  "D4":   {flag:"🇨🇻", name:"佛得角"},
  "D6":   {flag:"🇰🇲", name:"科摩罗"},
  "J5":   {flag:"🇬🇼", name:"几内亚比绍"},
  "3X":   {flag:"🇬🇳", name:"几内亚"},
  "9L":   {flag:"🇸🇱", name:"塞拉利昂"},
  "EL":   {flag:"🇱🇷", name:"利比里亚"},
  "TU":   {flag:"🇨🇮", name:"科特迪瓦"},
  "TZ":   {flag:"🇲🇱", name:"马里"},
  "XT":   {flag:"🇧🇫", name:"布基纳法索"},
  "5V":   {flag:"🇹🇬", name:"多哥"},
  "TY":   {flag:"🇧🇯", name:"贝宁"},
  "5T":   {flag:"🇲🇷", name:"毛里塔尼亚"},
  "6W":   {flag:"🇸🇳", name:"塞内加尔"},
  "C5":   {flag:"🇬🇲", name:"冈比亚"},
  "J3":   {flag:"🇬🇩", name:"格林纳达"},
  "9G":   {flag:"🇬🇭", name:"加纳"},
  "TR":   {flag:"🇬🇦", name:"加蓬"},
  "TJ":   {flag:"🇨🇲", name:"喀麦隆"},
  "TL":   {flag:"🇨🇫", name:"中非"},
  "TT":   {flag:"🇹🇩", name:"乍得"},
  "TN":   {flag:"🇨🇬", name:"刚果"},
  "9O":   {flag:"🇨🇩", name:"刚果民主共和国"}, "9P":   {flag:"🇨🇩", name:"刚果民主共和国"},
  "9Q":   {flag:"🇨🇩", name:"刚果民主共和国"},
  "S9":   {flag:"🇸🇹", name:"圣多美和普林西比"},
  "3C":   {flag:"🇬🇶", name:"赤道几内亚"},
};


// ============ Emoji 国旗 → ISO 3166-1 alpha-2 代码 ============
// 例如 🇨🇳 → cn，🇺🇸 → us
function emojiToIso2(emoji) {
  if (!emoji) return '';
  const cps = [...emoji].map(c => c.codePointAt(0));
  // Regional Indicator Symbol 从 U+1F1E6 开始（对应 A）
  if (cps.length < 2) return '';
  if (cps[0] < 0x1F1E6 || cps[0] > 0x1F1FF) return '';
  if (cps[1] < 0x1F1E6 || cps[1] > 0x1F1FF) return '';
  return String.fromCharCode(
    cps[0] - 0x1F1E6 + 65,
    cps[1] - 0x1F1E6 + 65
  ).toLowerCase();
}

// ============ 生成国旗 HTML（图片 + emoji fallback） ============
function flagHtml(country) {
  if (!country || !country.flag) return '';
  const iso2 = emojiToIso2(country.flag);
  if (!iso2) return `<span class="qso-flag-emoji" title="${country.name || ''}">${country.flag}</span>`;
  return `<img class="qso-flag-img" src="https://flagcdn.com/w20/${iso2}.png" srcset="https://flagcdn.com/w40/${iso2}.png 2x" alt="${country.name || ''}" title="${country.name || ''}" loading="lazy" decoding="async" onerror="this.style.display='none';this.nextElementSibling.style.display='inline';"><span class="qso-flag-emoji" style="display:none">${country.flag}</span>`;
}

// ============ 呼号解析函数 ============
// 取呼号前缀（最长匹配）查找国家
function lookupCallsignCountry(call) {
  if (!call) return null;
  const c = call.toUpperCase();
  // 跳过 "NOCALL" / "TEST" / 短号
  if (c.length < 2) return null;
  // 台湾地区呼号统一显示为“中国台湾”（使用中国国旗）
  if (/^(BM|BN|BO|BP|BQ|BU|BV|BW|BX)/.test(c)) return {flag:"🇨🇳", name:"中国台湾"};
  // 从长到短尝试匹配（支持 3H-3U、3Z、7J-7N、8J-8N 等 2-3 位前缀）
  for (let len = 3; len >= 1; len--) {
    if (c.length < len) continue;
    const prefix = c.substring(0, len);
    if (PREFIX_DB[prefix]) return PREFIX_DB[prefix];
  }
  return null;
}

/* ==================== 射频功率档位 -> TxGain 联动 ==================== */
var RF_GAIN_MAP = {'0': 10, '7': 30};      /* 小功率(0dB) / 大功率(7dB) */
function rfPowerSync(){
  var sel = $id('rfPower');
  if(!sel) return;
  if(sel.value !== '0' && sel.value !== '7') sel.value = '7';   /* 仅 0 / 7 有效，其余按大功率显示 */
}
function rfPowerChanged(){
  var sel = $id('rfPower'), tg = $id('rfTxGain');
  if(!sel) return;
  var v = String(sel.value), g = RF_GAIN_MAP[v];
  if(tg && g != null) tg.value = g;
  if(typeof toast === 'function'){
    toast('已选择' + (v === '0' ? '小功率（0 dBm / 1 mW）' : '大功率（约 +7 dBm）')
          + '，TxGain 自动设为 ' + (g == null ? '-' : g + ' dB'));
  }
}
function $id(x){return document.getElementById(x)}
function toast(msg, kind){   /* TOAST_V2 ★ 居中弹窗：淡入 → 停留 2 秒 → 渐渐淡出 */
  var t = document.getElementById('toast');
  if(!t){
    t = document.createElement('div');
    t.id = 'toast';
    document.body.appendChild(t);
  }
  var txt = String(msg == null ? '' : msg);
  if(txt.length > 300) txt = txt.substring(0, 300) + '…';
  t.textContent = txt;
  t.className = (kind === false || kind === 'error' || kind === 'bad') ? 'error'
              : ((kind === true || kind === 'ok') ? 'ok' : '');
  t.style.cssText = 'position:fixed;left:50%;bottom:24px;top:auto;' +
    'transform:translateX(-50%) translateY(10px) scale(.98);z-index:99999;' +
    'background:rgba(13,21,32,.72);border:1px solid rgba(var(--theme-rgb),.45);color:#eaf2ff;' +
    'padding:10px 16px;border-radius:12px;text-align:center;' +
    'box-shadow:0 12px 34px rgba(0,0,0,.38),0 0 18px rgba(var(--theme-rgb),.14);' +
    'backdrop-filter:blur(14px) saturate(140%);-webkit-backdrop-filter:blur(14px) saturate(140%);' +
    'font-size:14px;font-weight:600;letter-spacing:.2px;' +
    'max-width:min(420px,82vw);max-height:26vh;overflow:hidden;' +
    'word-break:break-word;white-space:pre-wrap;line-height:1.5;' +
    'display:block;opacity:0;pointer-events:none;' +
    'transition:opacity .22s ease,transform .22s ease';
  t.style.boxShadow = '0 24px 64px rgba(0,0,0,.7)' +
    (t.className === 'ok' ? ',0 0 30px rgba(16,185,129,.25)'
     : (t.className === 'error' ? ',0 0 30px rgba(239,68,68,.25)' : ''));
  clearTimeout(window.__tt1); clearTimeout(window.__tt2);
  requestAnimationFrame(function(){                  /* 淡入 */
    t.style.opacity = '1';
    t.style.transform = 'translateX(-50%) translateY(0) scale(1)';
  });
  window.__tt1 = setTimeout(function(){              /* 2 秒后开始淡出 */
    t.style.opacity = '0';
    t.style.transform = 'translateX(-50%) translateY(10px) scale(.98)';
  }, 2000);
  window.__tt2 = setTimeout(function(){              /* 淡完再摘掉，避免挡点击 */
    t.style.display = 'none'; t.style.opacity = '0';
  }, 2400);
}

/* ==================== 频率与信道（统一设置）==================== */
let FREQ_PLAN = null;
const FREQ_MODES = ['DMR','D-STAR','YSF','P25','NXDN','FM','POCSAG','NONE'];

function freqHint(){
  const rx = FREQ_HZ(($id('fRx')||{}).value), tx = FREQ_HZ(($id('fTx')||{}).value);
  if($id('fRxMhz')) $id('fRxMhz').textContent = rx ? ('= ' + rx + ' Hz') : '';
  if($id('fTxMhz')) $id('fTxMhz').textContent = tx ? ('= ' + tx + ' Hz') : '';
}

async function freqLoad(){
  const now = $id('freqNow'), rows = $id('fRows');
  try{
    const r = await fetch('/api/mc/plan', {credentials:'same-origin'});
    const d = await r.json();
    if(!d || d.ok === false || !d.list){
      if(now) now.innerHTML = '<div class="mc-empty">读取失败：' + String((d&&(d.error||d.out))||r.status) + '</div>';
      return;
    }
    FREQ_PLAN = d;
      mcSeqBuild(d.list || []);   /* MC_DYN_CH 建立映射 */
    const list = d.list || [];
    const chN = parseInt(d.channels || list.length || 1, 10);
    if($id('fCh')) $id('fCh').value = chN;
    if($id('fRx')) $id('fRx').value = FREQ_MHZ(d.center_rx) || '';
    if($id('fTx')) $id('fTx').value = FREQ_MHZ(d.center_tx) || '';
    if($id('fGap')) $id('fGap').value = ((parseInt(d.spacing,10)||25000)/1000);
    freqHint();
    if(now){
      now.innerHTML = list.map(function(c){
        return '<span class="freq-chip"><b>通道 ' + mcSeq(c.n) + '</b>' + String(c.mode||'NONE')
          + ' · RX ' + mcMHz(c.rx) + ' / TX ' + mcMHz(c.tx) + '</span>';
      }).join('') || '<div class="mc-empty">暂无通道</div>';
    }
    if(rows){
      rows.innerHTML = list.map(function(c){
        const opts = FREQ_MODES.map(function(m){
          return '<option value="' + m + '"' + (m === c.mode ? ' selected' : '') + '>' + m + '</option>';
        }).join('');
        return '<div class="freq-row">'
          + '<span class="fr-n">通道 ' + c.n + '</span>'
          + '<select id="fMode' + c.n + '">' + opts + '</select>'
          + '<input id="fR' + c.n + '" type="text" inputmode="decimal" value="' + (FREQ_MHZ(c.rx)||'') + '" placeholder="RX MHz">'
          + '<input id="fT' + c.n + '" type="text" inputmode="decimal" value="' + (FREQ_MHZ(c.tx)||'') + '" placeholder="TX MHz">'
          + '<button class="btn" onclick="freqCalc(' + c.n + ')">按间距算</button>'
          + '</div>';
      }).join('');
    }
  }catch(e){
    if(now) now.innerHTML = '<div class="mc-empty">读取失败：' + e + '</div>';
  }
}

function freqCalc(n){
  const rx = parseInt(($id('fRx')||{}).value,10);
  const tx = parseInt(($id('fTx')||{}).value,10);
  const gap = parseInt(($id('fGap')||{}).value,10) || 0;
  var st=chGridStep(n);   /* RF 页拿不到采样率时按 250k 网格推导 */
  if(isFinite(rx) && $id('fR'+n)) $id('fR'+n).value = rx + st*gap;
  if(isFinite(tx) && $id('fT'+n)) $id('fT'+n).value = tx + st*gap;
}

async function freqSave(onlySave){
  const nCh = Math.max(1, Math.min(7, parseInt(($id('fCh')||{}).value,10) || 2));
  const cRx = parseInt(($id('fRx')||{}).value,10) || 0;
  const cTx = parseInt(($id('fTx')||{}).value,10) || 0;
  const gap = parseInt(($id('fGap')||{}).value,10) || 0;
  if(!cRx || !cTx){ toast('请先填写中心 RX/TX 频率', 'error'); return; }
  if(gap < 12500){ toast('通道间距太小（需 ≥ 12500 Hz）', 'error'); return; }
  const plan = {
    channels: nCh, center_rx: cRx, center_tx: cTx, spacing: gap,
    sample_rate: (FREQ_PLAN && FREQ_PLAN.sample_rate) || 150000,
    callsign: (FREQ_PLAN && FREQ_PLAN.callsign) || '',
    dmr_id: (FREQ_PLAN && FREQ_PLAN.dmr_id) || '',
    list: []
  };
  for(let i = 1; i <= nCh; i++){
    const el = $id('fR' + i), tl = $id('fT' + i), md = $id('fMode' + i);
    plan.list.push({
      n: i,
      mode: md ? md.value : 'NONE',
      rx: (el && parseInt(el.value,10)) || (cRx + (i-1)*gap),
      tx: (tl && parseInt(tl.value,10)) || (cTx + (i-1)*gap)
    });
  }
  const LEGAL = '请遵守所在国家或地区关于无线电发射设备、频率使用和操作资格的法律法规。因违规使用造成的后果由操作者承担，与本软件及作者无关。';
  if(!onlySave && !confirm('将写入 MMDVM-Multi.ini 与各通道配置，并重启受影响的通道。\n大约需要 20-60 秒。\n\n'+LEGAL+'\n\n继续？')) return;
  toast(onlySave ? '正在保存频率规划...' : '正在应用频率规划（约 20-60 秒）...');
  try{
    const r = await fetch('/api/mc/plan-' + (onlySave ? 'save' : 'apply'), {
      method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(plan)});
    const d = await r.json();
    const msg = String((d && (d.out || d.error)) || '').trim();
    if(d && d.ok) toast(((msg.slice(0, 260) || '已应用') + ' ｜ ' + LEGAL), 'ok');
    else toast('失败：' + (msg || r.status), 'error');
  }catch(e){ toast('失败：' + e, 'error'); }
  setTimeout(function(){ freqLoad(); if(typeof mcRefresh === 'function') mcRefresh(); }, 3000);
}

/* 各协议页顶部的「各通道频率」条 + 本页横幅（多信道：按各自通道显示） */
const PAGE_MODE = {dmr:'DMR', ysf:'YSF', p25:'P25', nxdn:'NXDN', dstar:'D-STAR',
                   fm:'FM', pocsag:'POCSAG', rf:'DMR'};
async function renderFreqStrips(){
  try{
    const d = await (await fetch('/api/mc/channels-lite', {credentials:'same-origin'})).json();
    const chs = (d && d.channels) || [];
    mcSeqBuild(chs);   /* MC_SEQ_HELPER */
    const txt = chs.map(function(c){
      return mcSeq(c.n) + '.' + String(c.mode||'NONE') + ' ' + mcMHz(c.rx) + '/' + mcMHz(c.tx);
    }).join('　|　') || '（未配置通道）';
    document.querySelectorAll('.freq-strip').forEach(function(e){ e.textContent = txt; });

    // 当前页横幅（RX/TX/差频）显示"本页协议所在通道"的频率，避免与多信道实际值不一致
    const page = document.body.dataset.theme || 'dashboard';
    const mode = PAGE_MODE[page];
    let pick = chs.filter(function(c){ return mode && String(c.mode) === mode; });
    if(!pick.length) pick = chs.slice(0, 1);
    const cur = pick[0];
    const sec = document.getElementById('page-' + page);
    if(cur && sec){
      const rx = parseInt(cur.rx,10) || 0, tx = parseInt(cur.tx,10) || 0;
      sec.querySelectorAll('.dyn-rx').forEach(function(e){ e.textContent = mcMHz(rx).replace(' MHz',''); });
      sec.querySelectorAll('.dyn-tx').forEach(function(e){ e.textContent = mcMHz(tx).replace(' MHz',''); });
      sec.querySelectorAll('.dyn-off').forEach(function(e){
        e.textContent = (rx && tx) ? ((tx - rx) >= 0 ? '+' : '') + ((tx-rx)/1e6).toFixed(3) : '--';
      });
    }
  }catch(e){}
}

/* ==================== 首页多信道状态卡（多路并行，模式在 Multi 页配置）==================== */
const MC_MODES = ['DMR','D-STAR','YSF','P25','NXDN','FM','POCSAG','NONE'];

/* ==================== ★ 全站统一频率约定（以后新增页面也照这个来）====================
   输入：按 MHz 小数习惯填写
        439.500  -> 439500000 Hz    （25 kHz 步进）
        439.5125 -> 439512500 Hz    （12.5 kHz 步进）
        也兼容直接输入 Hz（>= 100000 的整数按 Hz 处理）
   显示：439.500 / 439.5125（25k 用 3 位小数，12.5k 用 4 位）
   用法：FREQ_HZ(输入框的值) 得到 Hz；FREQ_MHZ(hz) 得到显示串        */
function FREQ_HZ(v){
  if(v===null||v===undefined) return 0;
  var s=String(v).trim().replace(/[^0-9.]/g,'');
  if(s==='') return 0;
  var f=parseFloat(s);
  if(!isFinite(f)||f<=0) return 0;
  var isMhz=(s.indexOf('.')>=0 || f<100000);
  return Math.round(isMhz ? f*1000000 : f);
}
function FREQ_MHZ(hz){
  hz=parseInt(hz,10);
  if(!isFinite(hz)||hz<=0) return '';
  var d=(hz%12500===0 && hz%25000!==0) ? 4 : 3;
  return (hz/1000000).toFixed(d);
}
function FREQ_TXT(hz){ var s=FREQ_MHZ(hz); return s? (s+' MHz') : '-'; }
if(typeof window!=='undefined'){ window.FREQ_HZ=FREQ_HZ; window.FREQ_MHZ=FREQ_MHZ; window.FREQ_TXT=FREQ_TXT; }

/* POCSAG 寻呼发射频率：界面填 MHz，提交时换算成 Hz（隐藏域 name=freq） */
function pocFreqTyped(){
  var s=(document.getElementById('pocMhz')||{}).value;
  var hz=FREQ_HZ(s), hid=document.getElementById('pocFreq');
  if(hid && hz) hid.value=String(hz);
  var t=document.getElementById('pocMhzHz');
  if(t) t.textContent = hz? ('= '+hz+' Hz') : '请填 MHz，例如 439.500 / 439.5125';
}

function mcMHz(v){ v = parseInt(v,10); return (isFinite(v) && v > 0) ? FREQ_TXT(v) : '-'; }
/* ★ 射频通道 1~5 各用一种鲜艳颜色（暗底上辨识度高） */
var MC_CH_COLORS = ['#22d3ee', '#4ade80', '#fbbf24', '#c084fc', '#fb7185'];
function mcChColor(n){
  n = parseInt(n, 10) || 1;
  return MC_CH_COLORS[((n - 1) % MC_CH_COLORS.length + MC_CH_COLORS.length) % MC_CH_COLORS.length];
}
/* MC_SEQ_HELPER ★ 统一通道编号：物理通道号 → 显示序号（1..N，跳过 NONE）
   —— 首页「频率模式列表」、通联列表、Multi 看板、日志选择器全部用这一个映射，
      避免各处编号不一致（历史上出现过 CH4=FM 却显示通道3 的错位）。 */
var MC_SEQ_MAP = {};      /* 物理号 -> 显示序号 */
var MC_SEQ_CHS = [];      /* 最近一次 /api/mc/channels 的通道表 */
function mcSeqBuild(chs){
  MC_SEQ_MAP = {};
  MC_SEQ_CHS = chs || [];
  var k = 0;
  (chs || []).forEach(function(c){
    if(String(c.mode || 'NONE').toUpperCase() === 'NONE') return;
    k++;
    MC_SEQ_MAP[Number(c.n)] = k;
  });
  return MC_SEQ_MAP;
}
function mcSeq(n){          /* 取显示序号；没映射时回退到原号 */
  var v = MC_SEQ_MAP[Number(n)];
  return v ? v : n;
}
function MC_DYN_CH(){      /* MC_DYN_CH ★ 规划页可用通道数：优先用 plan 的，其次数通道表 */
  var n = parseInt((PLN && PLN.channels) || 0, 10);
  if(n > 0) return n;
  return Math.max(1, (MC_SEQ_CHS || []).length);
}
function mcModeOf(n){       /* 物理号 -> 模式名 */
  var c = MC_SEQ_CHS.filter(function(x){ return Number(x.n) === Number(n); })[0] || {};
  return String(c.mode || '').toUpperCase();
}

/* ★★★ 模式图标（统一 40×40，圆角方牌，风格一致）★★★ */
var MC_ICON_COLORS = {
  'DMR':'#0ea5e9','D-STAR':'#8b5cf6','YSF':'#f97316','NXDN':'#2563eb',
  'P25':'#e11d48','FM':'#10b981','POCSAG':'#f59e0b','NONE':'#64748b'
};
function mcIconSvg(mode){
  var m = String(mode||'').toUpperCase(), col = MC_ICON_COLORS[m] || '#64748b';
  var g = '';
  if(m === 'DMR'){
    g = '<rect x="9" y="14" width="22" height="4" rx="2" fill="#fff"/>'
      + '<rect x="9" y="22" width="14" height="4" rx="2" fill="#fff" opacity=".75"/>';
  } else if(m === 'D-STAR'){
    g = '<path d="M20 10l2.6 6.1 6.4.6-4.9 4.3 1.5 6.4L20 24l-5.6 3.4 1.5-6.4-4.9-4.3 6.4-.6z" fill="#fff"/>';
  } else if(m === 'YSF'){
    g = '<path d="M20 10l8 10-8 10-8-10z" fill="#fff"/>';
  } else if(m === 'NXDN'){
    g = '<path d="M13 27V13h4.2l5.6 8V13H27v14h-4.2l-5.6-8v8z" fill="#fff"/>';
  } else if(m === 'P25'){
    g = '<path d="M20 10l9 3.4v7.1c0 5-3.7 8.2-9 9.5-5.3-1.3-9-4.5-9-9.5v-7.1z" fill="#fff"/>';
  } else if(m === 'FM'){
    g = '<path d="M10 20c2.5-6 5-6 7.5 0s5 6 7.5 0 5-6 5 0" stroke="#fff" stroke-width="3" fill="none" stroke-linecap="round"/>';
  } else if(m === 'POCSAG'){
    g = '<rect x="13" y="11" width="14" height="18" rx="3" fill="#fff"/>'
      + '<rect x="16" y="14" width="8" height="6" rx="1" fill="' + col + '"/>';
  } else {
    g = '<circle cx="20" cy="20" r="6" fill="#fff" opacity=".7"/>';
  }
  return '<svg class="mc-icon" viewBox="0 0 40 40" width="40" height="40" '
       + 'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="' + m + '">'
       + '<rect x="2" y="2" width="36" height="36" rx="9" fill="' + col + '"/>'
       + g + '</svg>';
}
/* ★ 在跑：图标 + 模式名；未在跑：只显示模式名 */
function mcModeCell(mode, running){
  if(running){
    return '<span style="display:flex;align-items:center;gap:8px" data-mode="'+mode+'">'
         + mcIconSvg(mode) + '<span class="mc-mode-txt">' + mode + '</span></span>';
  }
  return '<span class="mc-mode-txt">' + mode + '</span>';
}
var MC_HIDE_TX = {};
  try{
    const d = await r.json();
    const c = (d && d.carrier) || {};
    if(c.tx) MC_HIDE_TX[String(c.tx)] = 1;
    if(d && d.cfg && d.cfg.dual_carrier && d.secondary && d.secondary.tx)
      MC_HIDE_TX[String(d.secondary.tx)] = 1;
  }catch(e){}
}

async function mcRefresh(){
  const box = $id('mcMatrix');
  if(!box) return;
  try{
    const r = await fetch('/api/mc/channels?light=1', {credentials:'same-origin'});
    const d = await r.json();
    if(!d || d.ok === false){
      box.innerHTML = '<div class="mc-empty">读取失败：'
        + String((d && (d.error || d.out)) || r.status) + '</div>';
      return;
    }
    const chs = d.channels || [];
    if($id('mcCount')){
      $id('mcCount').textContent =
        chs.filter(function(c){ return c.mode && String(c.mode).toUpperCase() !== 'NONE'; }).length + ' 路';
    }
    if(!chs.length){ box.innerHTML = '<div class="mc-empty">没有已配置的通道</div>'; return; }
    /* ★★★ 只显示「已启用模式」的通道（隔离频点不列出）★★★
       并按 1..N 顺序重新编号，不出现跳号 */
    const MC_SEQ_NUM = true;
    const list = chs.filter(function(c){
      if(!c.mode || String(c.mode).toUpperCase() === 'NONE') return false;
      return true;
    });
    if(!list.length){ box.innerHTML = '<div class="mc-empty">没有使用中的通道</div>'; return; }
    box.innerHTML = list.map(function(c, i){
      const idx  = String(i + 1);                                     /* ★ 显示序号 1..N */
      const has  = true;                                              /* 列表已过滤，都是启用中 */
      const run  = !!c.iq && !!c.host;                                /* 确实在跑 */
      const dotc = run ? '#10b981' : (has ? '#f59e0b' : '#64748b');
      const right = has ? mcModeCell(String(c.mode), run)
                        : '<span class="mc-off">未启用</span>';
      const freq = has
        ? 'RX '+mcMHz(c.rx)+' ｜ TX '+mcMHz(c.tx)
        : '<span style="color:#7d93b2">— 该频点留作隔离，不承载业务 —</span>';
      return '<div class="mc-card'+(has ? ' on' : ' off')+'">'
        + '<div class="mc-hd"><span><span class="mc-dot" style="background:'+dotc+'"></span>'
        + '<b style="color:'+mcChColor(c.n)+';font-weight:800">射频通道 '+idx+'</b></span>'
        + right + '</div>'
        + '<div class="mc-freq">'+freq+'</div>'
        + '</div>';
    }).join('');
  }catch(e){
    box.innerHTML = '<div class="mc-empty">读取失败：'+e+'</div>';
  }
}
async function mcPost(url, body, okMsg){
  try{
    const r = await fetch(url, {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(body || {})});
    const d = await r.json();
    const ok = !!(d && d.ok);
    toast(ok ? okMsg : ('失败：'+String((d && (d.error || d.out)) || r.status)), ok ? 'ok' : 'error');
  }catch(e){ toast('失败：'+e, 'error'); }
  setTimeout(mcRefresh, 1600);
}
/* QSO_SYNC_FIX ★ 切换模式/保存规划后，强制丢弃通道表缓存，让通联列表立刻跟随 */
function mcInvalidateChannels(){
  try{ if(typeof QSO_CH_CACHE === 'object'){ QSO_CH_CACHE.t = 0; QSO_CH_CACHE.list = []; } }catch(e){}
  try{ if(typeof FREQ_PLAN === 'object'){ /* 保留 */ } }catch(e){}
  setTimeout(function(){
    if(typeof mcRefresh === 'function') mcRefresh();
    if(typeof qsoRefresh === 'function') qsoRefresh();
  }, 600);
}

function mcRestart(n){
  var d = mcSeq(n) + (mcModeOf(n) ? ' (' + mcModeOf(n) + ')' : '');
  if(!confirm('重启通道 ' + d + ' 的基带与主机？')) return;
  toast('通道 ' + d + ' 正在重启 ...');
  mcPost('/api/mc/restart', {ch:n}, '通道 ' + d + ' 已重启');
}
function mcRestartAll(){
  if(!confirm('重启 MMDVM-Multi 引擎与全部通道？\n大约需要 20-40 秒。')) return;
  toast('正在重启全部通道 ...');
  mcPost('/api/mc/multi-restart', {}, 'MMDVM-Multi 与全部通道已重启');
}

/* ==================== BrandMeister 会话状态 ==================== */
async function bmCheck(){
  const b=$id('dmrStatusBadge'), t=$id('dmrStatusTitle'), det=$id('dmrStatusDetail');
  if(!b) return;
  const set=(txt,bg,fg,bd)=>{b.textContent=txt;b.style.background=bg;b.style.color=fg;b.style.borderColor=bd;};
  set('检测中','rgba(245,158,11,.18)','#fbbf24','rgba(245,158,11,.5)');
  if(t) t.textContent='正在读取 DMR 网关状态…';
  try{
    const rb=await fetch('/api/bm-status',{credentials:'same-origin'}).then(r=>r.json()).catch(()=>({}));
    const rg=await fetch('/api/gateway-status/dmr',{credentials:'same-origin'}).then(r=>r.json()).catch(()=>({}));
    const M={online:['已连接','rgba(16,185,129,.18)','#34d399','rgba(16,185,129,.5)'],
             closed:['被 Master 拒绝','rgba(239,68,68,.18)','#f87171','rgba(239,68,68,.5)'],
             auth:['密码错误','rgba(239,68,68,.18)','#f87171','rgba(239,68,68,.5)'],
             timeout:['连接超时','rgba(239,68,68,.18)','#f87171','rgba(239,68,68,.5)'],
             dns:['域名解析失败','rgba(239,68,68,.18)','#f87171','rgba(239,68,68,.5)'],
             unknown:['未连接','rgba(239,68,68,.18)','#f87171','rgba(239,68,68,.5)']};
    const m=M[rb.state]||M.unknown; set(m[0],m[1],m[2],m[3]);
    const srv=(rg&&rg.server)||'未选择';
    if(t) t.textContent='DMR 网关 · '+(rg&&rg.active?'服务运行中':'服务未运行')+' · 服务器 '+srv;
    if(det) det.textContent='Master='+String(rb.host||'-')+' ｜ DMR Id='+String(rb.id||'-')
      +' ｜ 呼号='+String(rb.callsign||'-')+' ｜ ready='+(rb.ready?'是':'否')+(rb.reason?(' ｜ '+rb.reason):'');
    const ds=$id('spDmrSel'); if(ds&&srv&&srv!=='未选择') ds.textContent=srv+(rb.state==='online'?'（已连接）':'');
  }catch(e){
    set('检测失败','rgba(239,68,68,.18)','#f87171','rgba(239,68,68,.5)');
    if(t) t.textContent='无法读取状态：'+e;
  }
}
function dmrRefreshStatus(){ bmCheck(); }

// 页面切换
function gwSet(mode,d){
  var b=$id(mode+'GwBadge'), s=$id(mode+'GwServer'), box=$id(mode+'GwStatus');
  if(!b||!s)return;
  var ok=!!(d&&d.connected), st=(d&&d.state)||'waiting';
  var isFm=(mode==='fm');
  var label=ok?'已连接':(st==='waiting'?'等待连接':(st==='active'?'已启动':'已断开'));
  b.textContent=label;
  var amber=(st==='waiting'||st==='active');
  b.style.background=ok?'rgba(16,185,129,.18)':(amber?'rgba(245,158,11,.18)':'rgba(239,68,68,.18)');
  b.style.color=ok?'#34d399':(amber?'#fbbf24':'#f87171');
  b.style.borderColor=ok?'rgba(16,185,129,.5)':(amber?'rgba(245,158,11,.5)':'rgba(239,68,68,.5)');
  var srv=(d&&d.server)||(isFm?'未配置':'未选择');
  var act=(d&&d.active)?'服务运行中':'服务未运行';
  var detail=(d&&d.detail)?(' · '+d.detail):'';
  var prefix=isFm?'FMO 服务器：':'服务器/反射器：';
  s.textContent=prefix+srv+' · '+act+detail;
  if(box)box.title=s.textContent;
  /* 同步页面上的“当前选择”显示，避免已连接却仍显示未选择 */
  if(mode==='dmr'){
    var ds=$id('spDmrSel');
    if(ds && srv && srv!=='未选择') ds.textContent=srv+(ok?'（已连接）':'');
  } else if(mode==='ysf'){
    var ys=$id('spYsfSel');
    if(ys && srv && srv!=='未选择') ys.textContent=srv+(ok?'（已连接）':(amber?'（连接中）':'（未连接）'));
  } else if(mode==='dstar'){
    var xs=$id('spDstarSel');
    if(xs && srv && srv!=='未选择') xs.textContent=srv+(ok?'（已连接）':(st==='active'?'（网关已启动）':''));
  }
}
function loadGwStatus(mode){
  window.__gwTimers=window.__gwTimers||{};
  clearTimeout(window.__gwTimers[mode]);
  fetch('/api/gateway-status/'+mode,{credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(d){gwSet(mode,d||{});
      var _theme=(mode==='fm'?'fmo':mode);
      if(document.body.dataset.theme===_theme)window.__gwTimers[mode]=setTimeout(function(){loadGwStatus(mode);},5000);
    }).catch(function(){gwSet(mode,{state:'disconnected',connected:false,active:false,server:''});});
}
// [1.0.163] local HTML escaper for DMR extra-channel UI
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
async function loadDmrChannels(){
  const wrap=$id('dmrExtraWrap'), box=$id('dmrChannelServers');
  if(!wrap||!box) return;
  try{
    const d=await fetch('/api/dmr/channels',{credentials:'same-origin'}).then(r=>r.json());
    const items=(d.items||[]).filter(x=>Number(x.n)>1);
    wrap.style.display = items.length ? '' : 'none';
    // [1.0.164] per-channel DMR identity UI
    box.innerHTML = items.map(x=>{
      const essidOn = x.essid ? ' checked' : '';
      const rx = Number(x.rx || 0) / 1000000;
      const tx = Number(x.tx || 0) / 1000000;
      const freq = (rx && tx) ? ' · RX '+rx.toFixed(4)+' / TX '+tx.toFixed(4)+' MHz' : '';
      return `<div class="dmr-ch" data-n="${x.n}" style="border:1px solid rgba(16,185,129,.28);border-radius:10px;padding:12px;margin:10px 0">
        <div class="sp-title" style="margin-bottom:8px">射频通道 ${x.n}${freq}</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px">
          <div class="fld"><label>电台呼号</label><input data-k="callsign" value="${esc(x.callsign||'')}" placeholder="BG2GA" autocomplete="off"></div>
          <div class="fld"><label>DMR ID（7 位）</label><input data-k="id" value="${esc(x.id||'')}" inputmode="numeric" placeholder="4600111" autocomplete="off"></div>
          <div class="fld"><label>ESSID 扩展</label>
            <div style="display:flex;align-items:center;gap:8px">
              <label style="display:flex;align-items:center;gap:5px;font-size:13px;color:#9fb6d6"><input type="checkbox" data-k="essidOn"${essidOn}>启用</label>
              <input data-k="essid" value="${esc(x.essid||'01')}" maxlength="2" inputmode="numeric" style="width:70px">
            </div>
          </div>
          <div class="fld"><label>色码 ColorCode</label><input data-k="cc" type="number" min="0" max="15" value="${esc(x.cc||'1')}"></div>
          <div class="fld"><label>Master 主机地址</label><input data-k="host" value="${esc(x.host||'')}" placeholder="域名或 IP" autocomplete="off"></div>
          <div class="fld"><label>Master 端口</label><input data-k="port" type="number" value="${esc(x.port||'62031')}"></div>
          <div class="fld"><label>热点连接密码</label><input data-k="password" value="${esc(x.password||'')}" placeholder="留空保持" autocomplete="off"></div>
        </div>
      </div>`;
    }).join('');
  }catch(e){ wrap.style.display='none'; }
}
function switchPage(name){
    try{ if(name && name !== 'dashboard'){ if(location.hash !== '#'+name) history.replaceState(null,'',location.pathname+location.search+'#'+name); } else if(location.hash){ history.replaceState(null,'',location.pathname+location.search); } }catch(e){}
    if(name === 'rfmode'){ var _f = $id('rfmFrame'); if(_f && !_f.src) _f.src = _f.getAttribute('data-src'); }
  if(name === 'fmo'){ var _f2 = $id('fmoFrame'); if(_f2 && !_f2.src) _f2.src = archThemeIframeUrl(_f2.getAttribute('data-src') || '/fmo?embed=1'); }
  if(name === 'dmr'){ loadDmrChannels(); }
  document.body.dataset.theme = name;
  document.querySelectorAll('.page').forEach(p=>p.style.display='none');
  let el = $id('page-'+name);
  if(!el && name !== 'dashboard'){          // 兼容旧缓存的半截页面：回退到首页并提示
    el = $id('page-dashboard');
    name = 'dashboard';
    if(typeof toast === 'function') toast('页面已更新，请按 Ctrl+F5 强制刷新浏览器缓存', 'error');
  }
  if(el) el.style.display = 'block';
  else if(name !== 'dashboard'){                     // 找不到区块：多为浏览器缓存了半截页面
    el = $id('page-dashboard');
    if(el) el.style.display = 'block';
    if(typeof toast === 'function') toast('页面不完整，请按 Ctrl+F5 强制刷新', 'error');
    try{ window.__repErr && window.__repErr('nav', 'missing page-' + name, location.href, '', ''); }catch(e){}
    return;
  }
  if(el && !el.children.length){                     // 区块在但没有内容 -> 自动重载一次拿新页面
    try{
      if(!sessionStorage.getItem('_heal')){
        sessionStorage.setItem('_heal', '1');
        if(typeof toast === 'function') toast('页面内容缺失，正在自动重新加载…');
        setTimeout(function(){ location.reload(true); }, 600);
        return;
      }
    }catch(e){}
  }
  document.querySelectorAll('.sb-item').forEach(x=>{
    x.classList.toggle('active', x.dataset.page === name);
  });
  if(name === 'ports') fetchPorts();
  if(['dmr','ysf','p25','nxdn','dstar'].indexOf(name) >= 0 && typeof loadGwStatus === 'function') loadGwStatus(name);
  if(name === 'fmo' && typeof loadGwStatus === 'function') loadGwStatus('fm');
  if(name === 'sys' && typeof sysLoad === 'function'){ sysLoad(); }
  if(name === 'calls' && typeof callsLoad === 'function'){ callsLoad(); if(typeof callsApplyAuto === 'function') callsApplyAuto(); }
  if(name !== 'calls' && typeof callsStopAuto === 'function') callsStopAuto();
  if(name === 'dashboard'){ fetchStatus(); mcRefresh(); }
  /* ★ 卡片已移到 rf 页：rf 和 freq 都要触发加载 */
    if((name === 'rf' || name === 'freq') && typeof freqLoad === 'function') freqLoad();
  if(typeof renderFreqStrips === 'function') setTimeout(renderFreqStrips, 400);
  if(name === 'multi'){
    const f = $id('multiFrame');
    if(f && String(f.src || '').indexOf('/multi') < 0) f.src = '/multi?embed=1';
  }
  const PMAP = {rf:'RF', dmr:'DMR', ysf:'YSF', p25:'P25', nxdn:'NXDN',
                dstar:'D-STAR', fm:'FM', pocsag:'POCSAG'};
  if(PMAP[name] && typeof prefill === 'function') prefill(PMAP[name]);
  if(name === 'dmr') setTimeout(bmCheck, 300);
  if(name === 'rf' && typeof rfPowerSync === 'function') setTimeout(rfPowerSync, 500);
  // ★ 探针：把该页在浏览器里的真实内容长度与尺寸上报（远程排查用）
  try{
    if(window.__repErr && el){
      var _r = el.getBoundingClientRect();
      var _cs = window.getComputedStyle ? getComputedStyle(el) : null;
      window.__repErr('nav', 'page=' + name
        + ' kids=' + el.children.length
        + ' html=' + ((el.innerHTML || '').length)
        + ' rect=' + Math.round(_r.width) + 'x' + Math.round(_r.height)
        + ' disp=' + (_cs ? _cs.display : '?')
        + ' vis=' + (_cs ? _cs.visibility : '?')
        + ' op=' + (_cs ? _cs.opacity : '?')
        + ' bodytheme=' + (document.body.dataset ? document.body.dataset.theme : '?'), location.href, '', '');
    }
  }catch(e){}
}
document.querySelectorAll('.sb-item').forEach(el=>{
  el.addEventListener('click', ()=>{
    var p = el.dataset.page;
    switchPage(p);
  });
});
// 频率输入实时换算 MHz 提示
['fRx','fTx'].forEach(function(id){
  const el = $id(id);
  if(el) el.addEventListener('input', function(){ if(typeof freqHint === 'function') freqHint(); });
});

// 模式切换
document.querySelectorAll('.mode-pill').forEach(b=>{
  b.addEventListener('click', async ()=>{
    const m = b.dataset.mode;
    if(!confirm('确认切换到 ' + m + ' 模式？\n\n系统会停止当前协议并启动目标网关，可能需要 10-30 秒。')) return;
    toast('切换热点模式至 ' + m + '...');

    try {
      const r = await fetch('/api/switch/' + encodeURIComponent(m), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin'
      });

      if (!r.ok) {
        // 尝试读错误消息，但也限制长度
        let errMsg = 'HTTP ' + r.status;
        try {
          const ct = r.headers.get('content-type') || '';
          if (ct.includes('application/json')) {
            const d = await r.json();
            errMsg = d.message || errMsg;
          }
        } catch (e) {}
        throw new Error(String(errMsg).substring(0, 200));
      }

      // 只解析 JSON；非 JSON 不解析
      let d = {};
      const ct = r.headers.get('content-type') || '';
      if (ct.includes('application/json')) {
        try { d = await r.json(); } catch (e) {}
      }

      toast(d.message || ('已切换到 ' + m));
      setTimeout(fetchStatus, 1500);
    } catch (e) {
      toast('切换失败: ' + (e.message || e).toString().substring(0, 200));
    }
  });
});

// 主状态刷新

// ============ BM 服务器列表加载 ============
const BM_FLAG = {
  "CN": "🇨🇳", "AU": "🇦🇺", "AT": "🇦🇹", "BE": "🇧🇪", "BG": "🇧🇬",
  "BR": "🇧🇷", "CA": "🇨🇦", "CH": "🇨🇭", "CL": "🇨🇱", "CZ": "🇨🇿",
  "DE": "🇩🇪", "DK": "🇩🇰", "ES": "🇪🇸", "FI": "🇫🇮", "FR": "🇫🇷",
  "GB": "🇬🇧", "GR": "🇬🇷", "HU": "🇭🇺", "IE": "🇮🇪", "IL": "🇮🇱",
  "IT": "🇮🇹", "KR": "🇰🇷", "MX": "🇲🇽", "MY": "🇲🇾", "NL": "🇳🇱",
  "NO": "🇳🇴", "PH": "🇵🇭", "PL": "🇵🇱", "PT": "🇵🇹", "RO": "🇷🇴",
  "RU": "🇷🇺", "SE": "🇸🇪", "SI": "🇸🇮", "US": "🇺🇸", "ZA": "🇿🇦",
};

async function loadBmListOld_unused(forceReload) {
  const sel = document.getElementById('dmrBmHost');
  if (!sel) return;
  const currentVal = sel.value || '';
  try {
    const r = await fetch('/api/bm-list', {
      headers: { 'Accept': 'application/json' },
      redirect: 'manual'
    });
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('application/json')) {
      // 返回了 HTML（可能被重定向到登录页）
      if (r.status === 0 || r.type === 'opaqueredirect') {
        if (forceReload) toast('需要登录才能加载 BM 列表');
      } else {
        const text = await r.text();
        if (forceReload) toast('BM 列表加载失败 (HTTP ' + r.status + ')');
        console.error('bm-list response:', text.substring(0, 300));
      }
      return;
    }
    const d = await r.json();
    const masters = d.masters || [];
    if (!Array.isArray(masters) || masters.length === 0) {
      if (forceReload) toast('BM 服务器列表为空');
      return;
    }

    // 按国家分组
    const byCountry = {};
    masters.forEach(m => {
      const c = m.country || 'XX';
      if (!byCountry[c]) byCountry[c] = [];
      byCountry[c].push(m);
    });

    // 排序：CN 排最前，其余按字母
    const countries = Object.keys(byCountry).sort((a, b) => {
      if (a === 'CN') return -1;
      if (b === 'CN') return 1;
      return a.localeCompare(b);
    });

    // 生成 option
    sel.innerHTML = countries.map(c => {
      const flag = BM_FLAG[c] || '🌐';
      const group = byCountry[c];
      return group.map(m => {
        const label = `${flag} ${m.region || c} · BM ${m.id} · ${m.host}`;
        return `<option value="${m.host}">${label}</option>`;
      }).join('');
    }).join('');

    // 恢复选中
    if ([...sel.options].some(o => o.value === currentVal)) {
      sel.value = currentVal;
    }
    if (forceReload) {
      toast(`BM 服务器列表已加载（共 ${masters.length} 个）`);
    }
  } catch (e) {
    if (forceReload) toast('加载 BM 列表失败: ' + e.message);
  }
}

function setTxt(id, val){
  const el = $id(id);
  if(el) el.textContent = (val === undefined || val === null) ? '' : String(val);
  return el;
}

async function fetchStatus(){
  try{
    const r = await fetch('/api/status?light=1');
    const d = await r.json();
    const sys = d.system || {};
    const profile = d.profile || '未知';

    // 顶部 pill（★ 一律走 setTxt 空值保护：缺元素不能影响后面的 QSO 渲染）
    setTxt('topMode', profile);
    setTxt('curMode', profile);

    // 模式按钮高亮
    document.querySelectorAll('.mode-pill').forEach(b=>{
      const on = b.dataset.mode === profile;
      b.classList.toggle('active', on);
      const st = b.querySelector('.st');
      if(st) st.textContent = on ? '运行中' : '切换';
    });

    // 侧边栏呼号
    setTxt('sbCall', sys.callsign || 'NOCALL');
    window.__stationCall = String(sys.callsign || '').trim().toUpperCase();

    // 统计卡
    setTxt('stCpu', sys.cpu_temp ? (sys.cpu_temp + ' °C') : '--');
    setTxt('stMem', sys.mem || '--');
    setTxt('stUptime', sys.uptime || '--');
    // ★ CPU 实时占用率（颜色分级）
    const cu = $id('stCpuUse');
    if(cu){
      const raw = String(sys.cpu_usage || '').trim();
      const pct = parseFloat(raw);
      cu.textContent = raw || '--';
      cu.title = '负载(1/5/15 分钟)：' + (sys.load || 'N/A') + '　（0.25 秒瞬时采样）';
      if(isNaN(pct)) cu.style.color = '';
      else if(pct >= 85) cu.style.color = '#f87171';
      else if(pct >= 60) cu.style.color = '#fbbf24';
      else cu.style.color = 'var(--green2)';
    }
    const sx = $id('stSx');
    if(sx && sys.sx_probe === 'OK'){
      sx.textContent = 'SoapySX OK';
      sx.style.color = 'var(--green2)';
    } else if(sx){
      sx.textContent = '未探测到';
      sx.style.color = '#fbbf24';
    }

    // DB 统计
    setTxt('dbStat', '收录 ' + (sys.bm_count||'540+') + ' BM 主机，'
      + (sys.ysf_count||'1,840+') + ' YSF 房间，' + (sys.p25_count||'320+') + ' P25 组');

    // RF 页的频率显示
    const rxHz = sys.rx_hz || 0;
    const txHz = sys.tx_hz || 0;
    const offHz = txHz - rxHz;
    if($id('rfCurRx')) $id('rfCurRx').textContent = rxHz ? fmtMhz(rxHz) : '--';
    if($id('rfCurTx')) $id('rfCurTx').textContent = txHz ? fmtMhz(txHz) : '--';
    if($id('rfCurOff')){
      const sign = offHz >= 0 ? '+' : '';
      $id('rfCurOff').textContent = (rxHz && txHz) ? (sign + (offHz/1e6).toFixed(3)) : '--';
    }

    // QSO：轻量状态不返回 qso，避免每 3 秒把实时通话列表清空重绘
    if(Array.isArray(d.qso)) renderQSO(d.qso, window.__qsoLive || []);
  }catch(e){
    // 连接失败
    const pill = document.querySelector('.status-pill');
    if(pill) pill.innerHTML = '<span class="dot" style="background:#ef4444"></span>连接中断';
  }
}

function fmtMhz(hz){
  const n = parseFloat(hz);
  if(!isFinite(n)) return '--';
  return (n/1e6).toFixed(4);
}

// 12 色渐变主题池（每条 QSO 循环使用）
const QSO_PALETTE = [
  ['#10b981','#34d399','16,185,129'],   // 翠绿
  ['#06b6d4','#22d3ee','6,182,212'],    // 青蓝
  ['#3b82f6','#60a5fa','59,130,246'],   // 蓝
  ['#8b5cf6','#a78bfa','139,92,246'],   // 紫罗兰
  ['#d946ef','#e879f9','217,70,239'],   // 品红
  ['#ec4899','#f472b6','236,72,153'],   // 玫瑰
  ['#f97316','#fb923c','249,115,22'],   // 橙红
  ['#f59e0b','#fbbf24','245,158,11'],   // 琥珀
  ['#84cc16','#a3e635','132,204,22'],   // 黄绿
  ['#14b8a6','#5eead4','20,184,166'],   // 翡翠
  ['#6366f1','#818cf8','99,102,241'],   // 靛蓝
  ['#f43f5e','#fb7185','244,63,94'],    // 深粉
];

/* ==================== Live QSO（多信道：每通道一行 × 4 宫格）==================== */
let QSO_CH_CACHE = {t:0, list:[]};
async function qsoChannels(){
  const now = Date.now();
  if(QSO_CH_CACHE.list.length && (now - QSO_CH_CACHE.t) < 30000) return QSO_CH_CACHE.list;   /* QSO_SYNC_FIX 8s→3s */
  try{
    const d = await (await fetch('/api/mc/channels-lite', {credentials:'same-origin'})).json();
    QSO_CH_CACHE = {t:now, list:((d && d.channels) || [])};
  }catch(e){}
  return QSO_CH_CACHE.list;
}
function qsoCard(q, idx){
  const berNum = parseFloat(q.ber);
  let berCls = 'good';
  if(berNum >= 5) berCls = 'bad';
  else if(berNum >= 2) berCls = 'warn';
  const srcCls = 'src-'+String(q.src||'').toLowerCase();
  const [c1, c2, rgb] = QSO_PALETTE[idx % QSO_PALETTE.length];
  const style = `--qso-c1:${c1};--qso-c2:${c2};--qso-rgb:${rgb}`;
  const country = (q && q.call_valid === false) ? null : lookupCallsignCountry(q.call);
  const countryHtml = country
    ? `<span class="qso-country">${flagHtml(country)}<span class="qso-country-name">${country.name}</span></span>`
    : '';
  const idHtml = q.id ? `<span class="qso-id" title="DMR ID">${q.id}</span>` : '';
  const live = !!((q && q.live) || String((q && q._live_source) || '') === 'event');
  const _liveCell = live ? ' qso-live' : '';
  const durHtml = live
    ? '<span class="qso-live-badge">通话中</span>'
    : ('时长 <b>' + _sysEsc(q.duration || '-') + '</b>');
  return `<div class="qso-item${_liveCell}" style="${style}">
      <div class="glow"></div>
      <div class="qso-row1">
        <span class="qso-call">${q.call}</span>
        ${countryHtml}
        ${idHtml}
        <span class="qso-tag mode">${q.mode}</span>
        <span class="qso-tag ${srcCls}">${q.src}</span>
        <span class="qso-time">${q.time}</span>
      </div>
      <div class="qso-row2">
        <span>目标：<b>${q.target}</b></span>
        <span>${durHtml}</span>
        <span>BER: <span class="ber ${berCls}">${q.ber}</span></span>
      </div>
    </div>`;
}
function qsoEmptyCell(){
  return '<div class="qso-cell-empty"><span class="dot"></span>守候中</div>';
}
// active only comes from the realtime event endpoint.
function qsoSplit(list){
  const arr = list || [];
  let active = null, last = null;
  for(let i = 0; i < arr.length; i++){
    const q = arr[i];
    if(q && q.live === true){
      if(!active) active = q;
    } else if(!last){
      last = q;
    }
    if(active && last) break;
  }
  return { active: active, last: last };
}

// ★ 一个「格」：槽位头(名称+状态点) + 主体(活动/最近) + 底部「上次」
function qsoSlotCell(label, list, idx){
  const sp = qsoSplit(list);
  const live = !!sp.active;
  const stateCls = live ? 'live' : 'idle';
  const stateTxt = live ? '通话中' : '空闲';
  let body;
  if(sp.active){
    body = qsoCard(sp.active, idx);
  } else if(sp.last){
    body = qsoCard(sp.last, idx);
  } else {
    body = qsoEmptyCell();
  }
  let prevHtml = '';
  if(sp.active && sp.last){
    prevHtml = '<div class="qso-prev">上次：<b>' + _sysEsc(sp.last.call || '-') + '</b>　' + _sysEsc(sp.last.duration || '') + '</div>';
  }
  return '<div class="qso-cell">'
    + '<div class="qso-slot-lbl">'
    + '<span class="qso-slot-dot ' + stateCls + '"></span>'
    + label
    + '<span class="qso-slot-state ' + stateCls + '">' + stateTxt + '</span>'
    + '</div>'
    + body
    + prevHtml
    + '</div>';
}
let QSO_RENDER_SIG = '';
async function renderQSO(items, liveItems){
  const box = $id('qsoList');
  if(!box) return;
  items = Array.isArray(items) ? items.filter(function(q){ return String((q && q.duration) || '') !== '通话中'; }) : [];
  const live = Array.isArray(liveItems) ? liveItems : (window.__qsoLive || []);
  window.__qsoLive = live;
  items = items.concat(live.map(function(q){
    return (q && q.live === false) ? Object.assign({}, q)
                                   : Object.assign({}, q, {live:true, duration:'通话中'});
  }));
  try{ renderQsoMap(items); }catch(e){  }
  const sig = JSON.stringify(items.map(function(q){
    return [q && q.ch, q && q.time, q && q.mode, q && q.call, q && q.id,
            q && q.target, q && q.src, q && q.duration, q && q.ber, q && q.live];
  }));
  if(sig === QSO_RENDER_SIG && box.children.length) return;
  QSO_RENDER_SIG = sig;

  // 1) 当前开启（在跑）的通道 —— 没开启的通道不显示
  let chs = (QSO_CH_CACHE && QSO_CH_CACHE.list) ? QSO_CH_CACHE.list.slice() : [];
  if(!chs.length){
    const seen = {};
    items.forEach(function(q){
      const m = /^CH(\d+)$/.exec(String(q.ch || ''));
      if(m && !seen[m[1]]){ seen[m[1]] = 1; chs.push({n:Number(m[1]), mode:q.mode || '', iq:1, host:1}); }
    });
    qsoChannels().then(function(list){
      if(list && list.length && typeof renderQSO === 'function') renderQSO(items, live);
    }).catch(function(){});
  }
  let nums = chs.filter(function(c){
    return String(c.mode||'NONE') !== 'NONE';
  }).map(function(c){ return Number(c.n); });
  if(!nums.length) nums = chs.map(function(c){ return Number(c.n); });

  // 2) 按通道分组（后端每条都带 [CHn] 标记）
  const byCh = {};
  items.forEach(function(q){
    const m = /^CH(\d)$/.exec(String(q.ch||''));
    const k = m ? Number(m[1]) : 0;
    (byCh[k] = byCh[k] || []).push(q);
  });
  if(!nums.length){
    nums = Object.keys(byCh).map(Number).filter(function(x){ return x > 0; }).sort();
  }
  /* ★ 物理通道号 → 显示序号（与首页「频率模式列表」一致） */
  mcSeqBuild(chs);      /* MC_SEQ_HELPER 统一编号 */
  if(!nums.length && byCh[0] && byCh[0].length) nums = [0];
  if(!nums.length){
    box.innerHTML = '<div class="qso-empty"><span class="dot"></span>当前信道守候中...</div>';
    return;
  }

  // ★ 新布局：DMR 占 2 格（时隙1/时隙2 各一格，各显示最新一条）；
  //   其它模式每模式一格；每行 2 个；DMR 排第一行；只显示开启的通道
  const modeOf = function(n){
    const c = chs.filter(function(x){ return Number(x.n) === n; })[0] || {};
    return String(c.mode || '').toUpperCase();
  };
  const ordered = nums.slice().sort(function(a, b){
    const da = (/^DMR/.test(modeOf(a))) ? 1 : 0;
    const db = (/^DMR/.test(modeOf(b))) ? 1 : 0;
    if(da !== db) return db - da;          // DMR 排最前
    return a - b;
  });
  const cells = [];
  let idx = 0;
  ordered.forEach(function(n){
    const currentMode = modeOf(n);
    const rawAll = byCh[n] || [];
    /* 任何通道随时都可能切换模式。通联列表只按实际通道分组，
       每条记录保留当时的真实模式，不再按当前模式丢弃历史记录。 */
    const all = rawAll.slice();
    const isDmr = /^DMR/.test(currentMode);
    if(isDmr){
      const s1 = all.filter(function(q){ return !/s2/i.test(String(q.mode || '')); });
      const s2 = all.filter(function(q){ return /s2/i.test(String(q.mode || '')); });
      cells.push(qsoSlotCell('时隙 1 (TS1)', s1, idx++));
      cells.push(qsoSlotCell('时隙 2 (TS2)', s2, idx++));
    } else {
      cells.push(qsoSlotCell(currentMode + ' 通道 ' + mcSeq(n), all, idx++));
    }
  });
  box.innerHTML = cells.length
    ? '<div class="qso-grid">' + cells.join('') + '</div>'
    : '<div class="qso-empty"><span class="dot"></span>当前信道守候中...</div>';
}

// 表单加载/保存
const FIELD_MAP = {
  rf:{callsign:'Callsign',id:'Id',rxfreq:'RXFrequency',txfreq:'TXFrequency',duplex:'Duplex',power:'Power',rxgain:'RxGain',txgain:'TxGain'},
  dmr:{callsign:'Callsign',id:'Id',essid:'ESSID',cc:'ColorCode',bm_host:'Address',bm_port:'Port'},
  ysf:{callsign:'Callsign',suffix:'Suffix',startup:'Startup',wiresx:'WiresXCommandPassthrough'},
  p25:{callsign:'Callsign',nac:'NAC',static:'Static'},
  nxdn:{callsign:'Callsign',ran:'RAN',protocol:'RptProtocol',static:'Static'},
  dstar:{gw_call:'gatewayCallsign',rep_call:'repeaterCall1',irc_user:'ircddbUsername'},
  fm:{callsign:'Callsign',ctcss:'CTCSSFrequency'},
  pocsag:{freq:'Frequency'},
};

async function loadForm(page){
  const form = $id('form-'+page);
  if(!form) return;
  try{
    const r = await fetch('/api/config/' + page.toUpperCase());
    const data = await r.json();
    const flat = {};
    Object.keys(data).forEach(sec=>{ Object.assign(flat, data[sec]); });
    const m = FIELD_MAP[page] || {};
    Object.keys(m).forEach(name=>{
      const inp = form.querySelector('[name="'+name+'"]');
      const val = flat[m[name]];
      if(inp && val !== undefined && inp.type !== 'password'){
        inp.value = val;
      }
    });
    const call = String(window.__stationCall || (($id('sbCall')||{}).textContent||'')).trim().toUpperCase();
    if(call && ['dmr','ysf','p25','nxdn','fm'].indexOf(page) >= 0){
      const inp = form.querySelector('[name="callsign"]');
      if(inp && !String(inp.value||'').trim()) inp.value = call;
    }
    if(call && page === 'dstar'){
      const gw=form.querySelector('[name="gw_call"]'); if(gw && !String(gw.value||'').trim()) gw.value=call;
      const rp=form.querySelector('[name="rep_call"]'); if(rp && !String(rp.value||'').trim()) rp.value=call;
      const iu=form.querySelector('[name="irc_user"]'); if(iu && !String(iu.value||'').trim()) iu.value=call.toLowerCase();
    }
    if(page === 'dmr'){
      const pw=form.querySelector('[name="bm_pw"]');
      if(pw && (!String(pw.value||'').trim() || String(pw.value||'').trim()==='CHANGE_ME')) pw.value='';
    }
  }catch(e){}
}

/* ===== 页面脚本错误可见化：任何 JS 异常都会在页面底部弹出红条 ===== */
window.__jsErrors = [];
function __showJsError(msg){
  try{
    window.__jsErrors.push(String(msg));
    var b = $id('jsErrBar');
    if(!b){
      b = document.createElement('div');
      b.id = 'jsErrBar';
      b.style.cssText = 'position:fixed;left:50%;top:50%;transform:translate(-50%,-50%) scale(.96);z-index:99999;background:rgba(13,21,32,.97);border:1px solid #2b3f57;color:#eaf2ff;padding:20px 34px;border-radius:14px;box-shadow:0 24px 64px rgba(0,0,0,.7),0 0 0 1px rgba(255,255,255,.05) inset;font-size:16px;font-weight:600;letter-spacing:.3px;text-align:center;max-width:min(560px,86vw);max-height:40vh;overflow-y:auto;word-break:break-word;white-space:pre-wrap;line-height:1.6;opacity:0;transition:opacity .3s ease,transform .3s cubic-bezier(.2,.9,.3,1.25);display:none' +
        'color:#fee2e2;font:13px/1.5 Consolas,monospace;padding:6px 10px;max-height:32vh;' +
        'overflow:auto;white-space:pre-wrap';
      document.body.appendChild(b);
    }
    b.textContent = '⚠ 页面脚本错误（请把这段反馈给开发者）：\n' +
      window.__jsErrors.slice(-6).join('\n');
  }catch(e){}
}
window.__showJsError = __showJsError;
window.addEventListener('error', function(e){
  __showJsError('Error: ' + (e.message || e.error) + '  @line ' + (e.lineno || '?'));
});
window.addEventListener('unhandledrejection', function(e){
  var r = e.reason || {};
  __showJsError('Promise: ' + (r.message || r));
});

/* ===== 内置自检：浏览器打开  http://<热点IP>/?selftest=1  会依次走一遍全部模式的保存 ===== */
async function __selfTest(){
  window.__selftestNoSwitch = true;
  var pages = ['rf','dmr','ysf','p25','nxdn','dstar','fm','pocsag'];
  var out = [];
  var box = document.createElement('pre');
  box.id = 'selftestOut';
  box.style.cssText = 'position:fixed;left:50%;top:50%;transform:translate(-50%,-50%) scale(.96);z-index:99999;background:rgba(13,21,32,.97);border:1px solid #2b3f57;color:#eaf2ff;padding:20px 34px;border-radius:14px;box-shadow:0 24px 64px rgba(0,0,0,.7),0 0 0 1px rgba(255,255,255,.05) inset;font-size:16px;font-weight:600;letter-spacing:.3px;text-align:center;max-width:min(560px,86vw);max-height:40vh;overflow-y:auto;word-break:break-word;white-space:pre-wrap;line-height:1.6;opacity:0;transition:opacity .3s ease,transform .3s cubic-bezier(.2,.9,.3,1.25);display:none' +
    'color:#bbf7d0;font:13px/1.5 Consolas,monospace;padding:8px;max-height:55vh;overflow:auto;' +
    'white-space:pre-wrap';
  document.body.appendChild(box);
  for(var i=0;i<pages.length;i++){
    var p = pages[i], t0 = Date.now(), msg = '';
    try{
      await saveForm({preventDefault:function(){}}, p);
      msg = (($id('toast') || {}).textContent || '').trim();
    }catch(e){ msg = 'THROW ' + (e && e.message); }
    out.push(p.toUpperCase() + ' -> ' + msg + '  (' + (Date.now()-t0) + 'ms)');
    box.textContent = out.join('\n');
  }
  out.push('JS 错误数: ' + ((window.__jsErrors || []).length));
  box.textContent = out.join('\n');
}
if(location.search.indexOf('selftest=1') >= 0){
  window.addEventListener('load', function(){ setTimeout(__selfTest, 1200); });
}

/* 兼容旧代码：loadStatus 实际不存在，统一指向 fetchStatus，避免抛异常 */
async function loadStatus(){
  try { if (typeof fetchStatus === 'function') await fetchStatus(); } catch (e) {}
}
window.loadStatus = loadStatus;

async function saveForm(ev, page){
  ev.preventDefault();
  const form = document.getElementById('form-' + page);
  if (!form) {
    toast('找不到表单', 'error');
    return;
  }

  const fd = new FormData(form);
  const body = {};
  fd.forEach((v, k) => { body[k] = v; });

  const modeMap = {
    'rf': 'RF', 'dmr': 'DMR', 'ysf': 'YSF', 'p25': 'P25',
    'nxdn': 'NXDN', 'dstar': 'D-STAR', 'fm': 'FM', 'pocsag': 'POCSAG',
  };
  const modeName = modeMap[page] || page.toUpperCase();
  const _stationCall = String(window.__stationCall || (($id('sbCall')||{}).textContent||'')).trim().toUpperCase();
  if(_stationCall && ['DMR','YSF','P25','NXDN','FM'].indexOf(modeName) >= 0 && !String(body.callsign||'').trim()) body.callsign=_stationCall;
  if(_stationCall && modeName === 'D-STAR'){
    if(!String(body.gw_call||'').trim()) body.gw_call=_stationCall;
    if(!String(body.rep_call||'').trim()) body.rep_call=_stationCall;
    if(!String(body.irc_user||'').trim()) body.irc_user=_stationCall.toLowerCase();
  }

  if (modeName === 'RF' && (!body.rxfreq || !body.txfreq)) {
    // RF 表单不再包含频率以外的台站 ID；频率缺失时从当前状态兜底，避免保存报错
    const st = (window.__lastStatus || {});
    body.rxfreq = body.rxfreq || String(st.rx || '') || '';
    body.txfreq = body.txfreq || String(st.tx || '') || '';
    body.id = body.id || String(st.radio_id || '') || '';
    body.duplex = body.duplex || '1';
  }
  if (modeName === 'DMR') {
    // ESSID 开关关闭时不要提交 essid，否则会被拼成 9 位 ID
    const on = $id('essidOn'), ev = $id('essidVal');
    if (on && !on.checked) { delete body.essid; }
    else if (on && ev && !String(body.essid || '').trim()) { body.essid = ev.value || '01'; }
    // DMR 表单没有频率字段 -> 从当前状态兜底，修复「点保存报错」
    if (!body.rxfreq || !body.txfreq) {
      const st = (window.__lastStatus || {});
      body.rxfreq = String(st.rx || '');
      body.txfreq = String(st.tx || '');
    }
    if (!body.callsign) body.callsign = ($id('sbCall') || {}).textContent || '';
    const _extras=[...document.querySelectorAll('#dmrChannelServers .dmr-ch')].map(el=>{
      const val=k=>{const e=el.querySelector('[data-k="'+k+'"]');return e?e.value:'';};
      const essOn=el.querySelector('[data-k="essidOn"]');
      const essid=(essOn&&essOn.checked)?(String(val('essid')||'01').trim()||'01'):'';
      return {n:Number(el.dataset.n),
              callsign:String(val('callsign')||'').trim(),
              id:String(val('id')||'').trim(),
              essid:essid,
              cc:String(val('cc')||'1').trim(),
              host:String(val('host')||'').trim(),
              port:String(val('port')||'62031').trim(),
              password:String(val('password')||'')};
    });
    body.channels=JSON.stringify([{
      n:1,
      callsign:String(body.callsign||'').trim(),
      id:String(body.id||'').trim(),
      essid:String(body.essid||'').trim(),
      cc:String(body.cc||'7').trim(),
      host:String(body.bm_host||'').trim(),
      port:String(body.bm_port||'62031').trim(),
      password:String(body.bm_pw||'')
    }].concat(_extras));
  }
  if (modeName === 'YSF' && (!body.rxfreq || !body.txfreq)) {
    const st = (window.__lastStatus || {});
    body.rxfreq = String(st.rx || '');
    body.txfreq = String(st.tx || '');
  }
  if (modeName === 'D-STAR') {
    /* DSTAR_MODULE_FIX:
       隐藏域的 name 是 reflector1（id=dstar_reflector），
       旧代码去读 body.dstar_reflector 永远是 undefined，
       于是 body.reflector1 被清成空串，后端看到空值就跳过不写，
       反射器模块就永远改不了（一直卡在原来的字母）。
       这里改为：反射器名取真实字段，模块字母取下拉框，重新拼。 */
    const _mdEl = $id('spDstarMod');
    let _mod = String((_mdEl && _mdEl.value) || body.dstar_module || 'A').trim().toUpperCase().slice(0, 1);
    if (!/^[A-Z]$/.test(_mod)) _mod = 'A';
    let _refl = String(body.reflector1 || '').trim().split(/\s+/)[0] || '';
    if (!_refl) {
      const _cf = $id('dstar_reflector');
      if (_cf) _refl = String(_cf.value || '').trim().split(/\s+/)[0] || '';
    }
    body.reflector1 = _refl ? (_refl + ' ' + _mod) : '';
    body.dstar_module = _mod;
    const su = $id('spDstarStartup');
    body.atStartup1 = (su && su.value === '0') ? '0'
                    : (String(body.dstar_startup || '1') === '0' ? '0' : '1');
    const ih = $id('dstarIrcHost');
    if (ih && ih.value) body.ircddb_host = ih.value;
    body.gw_call = body.gw_call || body.callsign || '';
    body.rep_call = body.rep_call || body.callsign || '';
    const rt = document.getElementById('dsRouting'); if (rt) body.callsign_routing = rt.value;
    const tm = document.getElementById('dsTime');    if (tm) body.time_announce   = tm.value;
    const xf = document.getElementById('dsXrf');     if (xf) body.use_dplus_xrf   = xf.value;
  }
  if (modeName === 'YSF') {
    const up = document.querySelector('[name="uppermode"]');
    if (up) body.uppermode = up.value;
  }

  toast('正在保存配置...');

  try {
    const r1 = await fetch('/api/save/' + modeName, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]') || {}).content || ''
      },
      body: JSON.stringify(body),
      credentials: 'same-origin'
    });

    if (!r1.ok) {
      // 后端失败时返回 500 + JSON，这里把真实原因原样显示给用户
      let em = '';
      try {
        const ctE = r1.headers.get('content-type') || '';
        if (ctE.includes('application/json')) {
          const dj = await r1.json();
          em = dj.message || dj.error || '';
        } else {
          em = (await r1.text()).substring(0, 200);
        }
      } catch (e) {}
      throw new Error(em || ('HTTP ' + r1.status));
    }

    let d1 = {};
    const ct1 = r1.headers.get('content-type') || '';
    if (ct1.includes('application/json')) {
      try { d1 = await r1.json(); } catch (e) {}
    }
    if (d1 && (d1.success === false || d1.ok === false)) {
      throw new Error(String(d1.message || d1.error || '后端拒绝保存').substring(0, 200));
    }
    const saveMsg = (d1 && d1.message) ? String(d1.message).substring(0, 80) : '配置已保存';
    toast('✓ ' + saveMsg, 'ok');

    // ★ 多信道设备：保存设置只保存，绝不自动切换模式。
    //   以前这里会调 /api/switch/<MODE>（只操作通道1），导致
    //   「保存 D-STAR → 通道1 变 D-STAR，DMR 停」「保存 DMR → D-STAR 停」。
    //   4 路射频必须能同时跑 4 个不同模式，模式归属只在 Multi 页/通道级切换里改。
    if (modeName !== 'NONE' && modeName !== 'RF') {
      toast('✓ ' + modeName + ' 设置已保存（不影响其它通道的运行模式）', 'ok');
    }

    await fetchStatus();
    if (typeof prefill === 'function' && modeName !== 'RF') {
      await prefill(modeName);
    }
    if (['dmr','ysf','p25','nxdn','dstar','fm'].indexOf(page) >= 0 && typeof loadGwStatus === 'function') loadGwStatus(page);
  } catch (e) {
    toast('保存失败: ' + e.message.substring(0, 150), 'error');
  }
}

function updateHint(rx, tx){
  const rn = parseInt(rx)/1e6, tn = parseInt(tx)/1e6;
  const h1 = $id('hint-rx');
  const h2 = $id('hint-tx');
  if(h1) h1.textContent = '= ' + rn.toFixed(4) + ' MHz';
  if(h2) h2.textContent = '= ' + tn.toFixed(4) + ' MHz（差频 ' + (tn-rn >= 0 ? '+' : '') + (tn-rn).toFixed(3) + ' MHz）';
}
document.addEventListener('input', e=>{
  if(e.target.name === 'rxfreq' || e.target.name === 'txfreq'){
    const form = e.target.closest('form');
    // ★ 空值保护：RF 页的频率输入已移除，不能再无脑取 .value
    const rxEl = form ? form.querySelector('[name="rxfreq"]') : null;
    const txEl = form ? form.querySelector('[name="txfreq"]') : null;
    if(rxEl && txEl && typeof updateHint === 'function') updateHint(rxEl.value, txEl.value);
  }
});

function onMasterChange(){ /* v2：已由智能选择器接管 */ }

// 端口
async function fetchPorts(){
  try{
    const r = await fetch('/api/ports');
    const data = await r.json();
    const box = $id('portList');
    const META = {
      '3334':['MMDVM-IQ','Modem IQ 符号基带流'],
      '3335':['MMDVM-Host','热点控制器核心通信'],
      '62031':['DMRGateway','BrandMeister 动态路由'],
      '62032':['DMR-Sub','MMDVM-Host DMR 套接字'],
      '3200':['MMDVM-Host','YSF 网络本地'],
      '4200':['YSFGateway','Yaesu System Fusion 端口'],
      '32010':['MMDVM-Host','P25 本地端口'],
      '42020':['P25Gateway','APCO Phase 1 专用网关'],
      '14020':['NXDNGateway','IDAS / Nexedge 网关'],
      '14021':['MMDVM-Host','NXDN 本地端口'],
      '20010':['MMDVM-Host','D-STAR 网络本地'],
      '20011':['ircDDBGateway','D-STAR ircDDB 全球路由'],
    };
    const keys = Object.keys(data).sort((a,b)=>+a-+b);
    box.innerHTML = keys.map(p=>{
      const it = data[p];
      const isL = it.state === 'LISTEN';
      const m = META[p] || ['--','--'];
      return `<div class="port-card">
        <div class="port-num">UDP ${p}</div>
        <div class="port-info">
          <b>${m[0]}</b>
          <span>${m[1]}</span>
        </div>
        <span class="port-badge ${isL?'listen':'closed'}">${isL?'LISTEN':'CLOSED'}</span>
      </div>`;
    }).join('');
  }catch(e){
    $id('portList').innerHTML = '<div class="qso-empty">加载失败</div>';
  }
}

async function updateDb(t){
  const c = $id('dbConsole');
  if(c) c.textContent = '[执行中] 正在更新 ' + t + ' ...';
  toast('正在更新 ' + t + '...');
  try{
    const r = await fetch('/api/update-db/' + t, {method:'POST'});
    const d = await r.json();
    toast(d.message || '更新完成');
    if(c) c.textContent = '[完成] ' + (d.message || '同步完成');
    try {
      if(t === 'ysfhosts' && typeof spRefresh === 'function') spRefresh('ysf');
      if((t === 'p25hosts' || t === 'nxdnhosts') && typeof fillHostPresets === 'function') fillHostPresets();
    } catch(e) {}
  }catch(e){ toast('异常: '+e); }
}

async function restartSvc(name){
  toast('正在重启 ' + name + '...');
  try{
    await fetch('/api/restart/' + name, {method:'POST'});
    toast(name + ' 已重启');
  }catch(e){ toast('失败: '+e); }
}

let _dmrReconnectBusy = false;
async function dmrReconnect(){
  if(_dmrReconnectBusy) return;
  const btn = (typeof event !== 'undefined' && event && event.target) ? event.target : null;
  const oldText = btn ? btn.textContent : '';
  if(btn){ btn.disabled = true; btn.textContent = '正在重连...'; }
  _dmrReconnectBusy = true;
  try{
    toast('正在重连 DMRGateway，请等待登录...');
    const r = await fetch('/api/restart/dmrgateway', {method:'POST', credentials:'same-origin'});
    const d = await r.json();
    toast(d.message || (d.ok ? '已提交重连' : '重连失败'), d.ok ? 'ok' : 'error');
    const deadline = Date.now() + 60000;
    while(Date.now() < deadline){
      await new Promise(x => setTimeout(x, 2000));
      const s = await fetch('/api/bm-status', {credentials:'same-origin'}).then(x => x.json());
      if(s.state === 'online'){ toast('DMRGateway 已登录 BrandMeister', 'ok'); break; }
      if(['auth','closed','dns','timeout'].includes(String(s.state))){
        toast(s.reason || 'DMR 登录失败', 'error'); break;
      }
    }
  }catch(e){ toast('重连失败：' + e, 'error'); }
  _dmrReconnectBusy = false;
  if(btn){ btn.disabled = false; btn.textContent = oldText || '连接 / 重连 DMRGateway'; }
  if(typeof bmCheck === 'function') setTimeout(bmCheck, 200);
}

// 启动（延迟到整段脚本执行完毕后再跑：否则会踩到后面 const SP/PRE_MAP 的暂时性死区）
function __boot(){
  var _p=(location.hash||'').replace(/^#/,'');
  if(_p && _p !== 'dashboard'){ setTimeout(function(){ if(typeof switchPage === 'function') switchPage(_p); },0); }
  else document.body.dataset.theme = 'dashboard';
  fetchStatus();
  if (typeof mcRefresh === 'function') setTimeout(mcRefresh, 50);   // 多信道矩阵优先加载
  setTimeout(function(){ loadBmList(false); loadForm('rf'); }, 2500);
  setInterval(()=>{
    if($id('page-dashboard').style.display !== 'none') fetchStatus();
  }, 10000);
  // ★ Live QSO：有通话变化时 0.8 秒快刷，空闲后自动退到 2.5 秒。
  (function(){
    let liveKey = '', live = [], history = [], historyAt = 0, historyBusy = false, liveVer = '';
    function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
    async function loadHistory(){
      if(historyBusy) return;
      historyBusy = true;
      try{
        const d = await (await fetch('/api/qso', {credentials:'same-origin'})).json();
        if(d && d.ok){ history = (d.qso || []).filter(function(q){ return String((q && q.duration) || '') !== '通话中'; }); renderQSO(history, live); }
      }catch(e){}
      historyAt = Date.now();
      historyBusy = false;
    }
    function applyLive(rows){
      const key = JSON.stringify(rows);
      if(key !== liveKey){
        liveKey = key; live = rows; window.__qsoLive = live;
        if(typeof renderQSO === 'function') renderQSO(history, live);
      }
    }
    // [FIX 1.0.132] long-poll: server answers the moment the state changes,
    // so the red cell appears/disappears in ~40 ms.  If the server is out of
    // wait slots (many clients) it answers at once and we back off to ~80 ms.
    async function liveLoop(){
      while(true){
        const p = $id('page-dashboard');
        if(!(p && p.style.display !== 'none')){ await sleep(400); continue; }
        const t0 = Date.now();
        let changed = false;
        try{
          const d = await (await fetch('/api/qso-live?since=' + encodeURIComponent(liveVer),
                            {credentials:'same-origin', cache:'no-store'})).json();
          if(d && typeof d.ver === 'string'){
            if(liveVer && d.ver !== liveVer) changed = true;
            liveVer = d.ver;
          }
          applyLive((d && d.qso) || []);
        }catch(e){ await sleep(400); continue; }
        if(!changed && (Date.now() - t0) < 120) await sleep(80);
        if(!historyAt || Date.now() - historyAt > 8000) loadHistory();
      }
    }
    loadHistory();
    liveLoop();
  })();
  setInterval(()=>{
    if($id('page-dashboard') && $id('page-dashboard').style.display !== 'none'
       && typeof mcRefresh === 'function') mcRefresh();                // 通道状态跟着刷新
  }, 15000);
}
window.__boot = __boot;
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', __boot);
} else {
  setTimeout(__boot, 0);
}

// ============================================================
// ★ 所有下拉框从 API 动态填充
// ============================================================

// 按关键字找到 preset-box 内的 select
function findSelByKeyword(keyword) {
  // 优先按 preset-box-lbl 匹配
  const boxes = document.querySelectorAll('.preset-box');
  for (const box of boxes) {
    const lbl = box.querySelector('.preset-box-lbl');
    if (lbl && lbl.textContent.includes(keyword)) {
      const sel = box.querySelector('select');
      if (sel) return sel;
    }
  }
  // 回退：按 label 匹配
  const flds = document.querySelectorAll('.fld');
  for (const fld of flds) {
    const lbl = fld.querySelector('label');
    if (lbl && lbl.textContent.includes(keyword)) {
      const sel = fld.querySelector('select');
      if (sel) return sel;
    }
  }
  return null;
}

// 通用填充函数
async function fillSel(keyword, apiUrl, formatFn) {
  const sel = findSelByKeyword(keyword);
  if (!sel) {
    console.debug('[fillSel] 未找到:', keyword);
    return;
  }
  const curVal = sel.value;
  try {
    const r = await fetch(apiUrl, {credentials: 'same-origin'});
    const d = await r.json();
    const items = d.items || d.masters || (Array.isArray(d) ? d : []);
    if (!items.length) {
      console.debug('[fillSel] 空数据:', keyword, apiUrl);
      return;
    }
    sel.innerHTML = items.map(formatFn).join('');
    // 恢复原选中值；找不到就用第一个
    if ([...sel.options].some(o => o.value === curVal)) {
      sel.value = curVal;
    } else if (sel.options.length) {
      sel.selectedIndex = 0;
    }
    console.log(`[fillSel] ${keyword}: ${items.length} items`);
  } catch (e) {
    console.warn('[fillSel] 失败:', keyword, e);
  }
}

// ★ 按 id 精确填充（不再依赖标签文字匹配 —— 之前 YSF 房间列表就是因为关键词匹配不上而从未生效）
async function fillSelById(id, apiUrl, formatFn, keepFirst) {
  const sel = document.getElementById(id);
  if (!sel) { console.debug('[fillSelById] 未找到:', id); return; }
  const curVal = sel.value;
  try {
    const r = await fetch(apiUrl, {credentials: 'same-origin'});
    const d = await r.json();
    const items = d.items || d.masters || (Array.isArray(d) ? d : []);
    if (!items.length) { console.debug('[fillSelById] 空数据:', id); return; }
    const head = (keepFirst && sel.options.length) ? sel.options[0].outerHTML : '';
    sel.innerHTML = head + items.map(formatFn).join('');
    if ([...sel.options].some(o => o.value === curVal)) sel.value = curVal;
    console.log(`[fillSelById] ${id}: ${items.length} items`);
  } catch (e) { console.warn('[fillSelById] 失败:', id, e); }
}

// D-STAR 特殊处理（带 optgroup 分组）
async function fillDstarSelOld_unused() {
  const sel = document.getElementById('dstarReflector')
        || findSelByKeyword('Default Reflector')
        || findSelByKeyword('默认连接反射器');
  if (!sel) { console.debug('[fillDstar] 未找到 select'); return; }
  const curVal = sel.value;
  try {
    const r = await fetch('/api/dstar-reflectors', {credentials: 'same-origin'});
    const d = await r.json();
    let html = '';
    // 中国/亚洲区优先
    const asiaKeys = ['REF', 'XRF', 'DCS', 'XLX'];
    // 提取可能匹配亚洲/中国的
    const isAsia = (name, desc) => {
      const t = (name + ' ' + (desc||'')).toUpperCase();
      return t.includes('HKG') || t.includes('CN') || t.includes('CHINA') 
          || t.includes('JAPAN') || t.includes('KOREA') 
          || t.includes('SINGAPORE') || t.includes('TAIWAN')
          || t.includes('ASIA') || t.includes('076') || t.includes('082')
          || t.includes('083') || t.includes('084') || t.includes('085');
    };
    // 中国/亚洲
    const asiaGroups = [];
    for (const key of asiaKeys) {
      const arr = d[key] || [];
      const asia = arr.filter(x => isAsia(x.name, x.ip));
      if (asia.length) {
        asiaGroups.push(`<optgroup label="🇨🇳 中国/亚洲 · ${key}">` +
          asia.slice(0, 200).map(x => 
            `<option value="${x.name}">${x.name} — ${x.ip||''}</option>`
          ).join('') + '</optgroup>');
      }
    }
    html += asiaGroups.join('');
    // 国际
    for (const key of asiaKeys) {
      const arr = d[key] || [];
      const intl = arr.filter(x => !isAsia(x.name, x.ip));
      if (intl.length) {
        html += `<optgroup label="🌍 国际 · ${key} (${intl.length})">` +
          intl.map(x => 
            `<option value="${x.name}">${x.name} — ${x.ip||''}</option>`
          ).join('') + '</optgroup>';
      }
    }
    if (html) {
      sel.innerHTML = html;
      if ([...sel.options].some(o => o.value === curVal)) {
        sel.value = curVal;
      }
      console.log('[fillDstar] total options:', sel.options.length);
    }
  } catch (e) {
    console.warn('[fillDstar] 失败:', e);
  }
}

// 全量填充
async function fillAllSelects() {
  // DMR BM Master
  await fillSel('DMR Master', '/api/bm-list', m => {
    const region = m.region ? `${m.region}` : (m.country || '');
    const id = m.id || '';
    const host = m.host || m;
    return `<option value="${host}">BM_${id}_${region} (${host})</option>`;
  });

  // P25 Startup Host
  await fillSel('P25 Startup Host', '/api/p25-hosts', p => {
    const name = p.name || '';
    const host = p.host || '';
    return `<option value="${name}">${name} (${host})</option>`;
  });

  // NXDN Host
  await fillSel('NXDN Host', '/api/nxdn-hosts', n => {
    const id = n.id || '';
    const host = n.host || '';
    return `<option value="${id}">${id} (${host})</option>`;
  });

  // D-STAR 反射器
  await fillDstarSel();

  // FCS Startup Room
  await fillSel('FCS Startup Room', '/api/fcs-hosts', f => {
    const id = f.id || '';
    const name = f.name || '';
    return `<option value="${id}">${id} - ${name}</option>`;
  });
}

// 页面加载后 500ms 填充（等 DOM 稳定）
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(fillAllSelects, 500);
});

// 切页时也填充（覆盖安全）
const __orig_switchPage = typeof switchPage === 'function' ? switchPage : null;
if (__orig_switchPage) {
  window.switchPage = function(name) {
    __orig_switchPage(name);
    setTimeout(fillAllSelects, 200);
  };
}

// 暴露到全局方便调试
window.fillAllSelects = fillAllSelects;


/* ==================== ★ 表单回填 / 预设（保存后能看到当前值） ==================== */
const PRE_MAP = {RF:'form-rf', DMR:'form-dmr', YSF:'form-ysf', P25:'form-p25',
                 NXDN:'form-nxdn', 'D-STAR':'form-dstar', FM:'form-fm', POCSAG:'form-pocsag'};

function stripCallSuffix(v){
  return String(v == null ? '' : v).replace(/\s+/g, ' ').trim().toUpperCase().replace(/\s+[A-Z]$/, '');
}

/* 静态通话组预设：点选 P25/NXDN 预设时写入 Static 字段 */
function applyStaticPreset(t, v){
  const el = $id(t === 'p25' ? 'p25Static' : 'nxdnStatic');
  if (!el || !v) return;
  const cur = String(el.value || '').split(',').map(s => s.trim()).filter(Boolean);
  if (cur.indexOf(v) < 0) cur.unshift(v);
  el.value = cur.join(', ');
  toast('已把通话组 ' + v + ' 加入静态列表');
}

/* 用 Pi-Star P25Hosts / NXDNHosts 补全预设下拉 */
async function fillHostPresets(){
  try {
    const r = await fetch('/api/p25-hosts', {credentials:'same-origin'});
    const d = await r.json();
    const items = d.items || [];
    const sel = $id('p25Preset');
    if (sel && items.length) {
      items.forEach(function (it) {   // 全量（Pi-Star P25Hosts 全部条目）
        const tg = String(it.id || '').replace(/^P25[-_]/i, '');
        if (!tg || !/^\d+$/.test(tg)) return;
        const o = document.createElement('option');
        o.value = tg; o.textContent = 'P25-' + tg + '  ' + (it.name || '');
        sel.appendChild(o);
      });
      const h = $id('p25HostHint');
      if (h) h.textContent = '已载入 Pi-Star P25Hosts 共 ' + items.length + ' 条（点选即写入静态通话组）';
    }
  } catch (e) {}
  try {
    const r2 = await fetch('/api/nxdn-hosts', {credentials:'same-origin'});
    const d2 = await r2.json();
    const items2 = d2.items || [];
    const sel2 = $id('nxdnPreset');
    if (sel2 && items2.length) {
      items2.forEach(function (it) {  // 全量（Pi-Star NXDNHosts 全部条目）
        const tg = String(it.id || '').replace(/^NXDN[-_]/i, '');
        if (!tg || !/^\d+$/.test(tg)) return;
        const o = document.createElement('option');
        o.value = tg; o.textContent = 'NXDN-' + tg + '  ' + (it.name || '');
        sel2.appendChild(o);
      });
      const h2 = $id('nxdnHostHint');
      if (h2) h2.textContent = '已载入 Pi-Star NXDNHosts 共 ' + items2.length + ' 条（点选即写入静态通话组）';
    }
  } catch (e) {}
}

/* D-STAR 三个网关开关的当前状态（标记文件类） */
async function fetchDstarFlags(){
  try {
    const r = await fetch('/api/dstar-flags', {credentials:'same-origin'});
    const d = await r.json();
    const rt = $id('dsRouting'); if (rt && d.callsign_routing != null) rt.value = String(d.callsign_routing);
    const t  = $id('dsTime');    if (t  && d.time_announce   != null) t.value  = String(d.time_announce);
    const x  = $id('dsXrf');     if (x  && d.use_dplus_xrf   != null) x.value  = String(d.use_dplus_xrf);
  } catch (e) {}
}

/* 从 /api/config/<mode> 回填表单，保证保存后能看到真实生效值 */
async function prefill(mode){
  const form = $id(PRE_MAP[mode] || '');
  if (!form) return;
  let flat = {};
  let cfg = {};
  try {
    const r = await fetch('/api/config/' + encodeURIComponent(mode), {credentials:'same-origin'});
    const d = await r.json();
    cfg = d || {};
    Object.keys(cfg).forEach(function (k) { Object.assign(flat, cfg[k] || {}); });
  } catch (e) { return; }
  const set = function (n, v) {
    const el = form.querySelector('[name="' + n + '"]');
    if (el && v != null && String(v) !== '') el.value = v;
  };

  if (mode === 'RF') {
    set('callsign', flat.Callsign);
    set('rxfreq', flat.RXFrequency); set('txfreq', flat.TXFrequency);
    set('rxgain', flat.RxGain); set('txgain', flat.TxGain);
    if (flat.RXFrequency && flat.TXFrequency) updateHint(flat.RXFrequency, flat.TXFrequency);
  } else if (mode === 'DMR') {
    set('callsign', flat.Callsign); set('cc', flat.ColorCode);
    set('bm_host', flat.Address); set('bm_port', flat.Port); set('bm_pw', flat.Password);
    const on = $id('essidOn'), ev = $id('essidVal');
    /* [FIX 1.0.86] 真正登录 BM 的是 DMRGateway 的 Id（可能是 9 位 = 7 位 ID + 2 位 ESSID）。
       Flask jsonify 会把 section 按字母序重排（DMRGATEWAY 在前、MMDVM_HOST 在后），
       直接合并会让 7 位的 Host Id 覆盖 9 位网关 Id，ESSID 勾选就被误清空。
       这里显式按 网关 > 主机 的优先级取 ID。 */
    const _gwId = String(((cfg.DMRGATEWAY || {}).Id) || ((cfg.DMR_NETWORK || {}).Id) || '');
    const _hostId = String((cfg.MMDVM_HOST || {}).Id || '');
    const idv = (_gwId || _hostId || String(flat.Id || '')).replace(/[^0-9]/g, '');
    if (idv) {
      if (idv.length >= 9) {
        set('id', idv.slice(0, 7));
        if (ev) ev.value = idv.slice(7);
        if (on) { on.checked = true; on.dispatchEvent(new Event('change')); }
      } else {
        set('id', idv);
        if (ev) ev.value = '01';
        if (on) { on.checked = false; on.dispatchEvent(new Event('change')); }
      }
    }
    const h = $id('dmrBmHost'); if (h && flat.Address) h.value = flat.Address;
    const s = $id('spDmrSel');
    if (s && flat.Address) s.textContent = flat.Address + ':' + (flat.Port || '62031');
  } else if (mode === 'YSF') {
    set('callsign', flat.Callsign); set('startup', flat.Startup);
    if (flat.WiresXCommandPassthrough != null) set('wiresx', String(flat.WiresXCommandPassthrough));
    if (flat.WiresXMakeUpper != null) set('uppermode', String(flat.WiresXMakeUpper));
  } else if (mode === 'P25') {
    set('callsign', flat.Callsign); set('nac', flat.NAC); set('static', flat.Static);
  } else if (mode === 'NXDN') {
    set('callsign', flat.Callsign); set('id', flat.Id); set('ran', flat.RAN);
    set('protocol', flat.Protocol || flat.RptProtocol); set('static', flat.Static);
  } else if (mode === 'D-STAR') {
    set('gw_call', stripCallSuffix(flat.gatewayCallsign || flat.Callsign));
    set('rep_call', stripCallSuffix(flat.repeaterCall1));
    set('irc_user', flat.ircddbUsername);
    const rt = $id('dsRouting');
    if (rt && flat.ircddbEnabled != null) rt.value = String(flat.ircddbEnabled);
    const ih = $id('dstarIrcHost');
    if (ih && flat.ircddbHostname) {
      let hit = false;
      Array.prototype.forEach.call(ih.options, function (o) { if (o.value === flat.ircddbHostname) hit = true; });
      if (!hit) {
        const o = document.createElement('option');
        o.value = flat.ircddbHostname; o.textContent = flat.ircddbHostname + '（当前配置）';
        ih.appendChild(o);
      }
      ih.value = flat.ircddbHostname;
    }
    if (flat.reflector1) {
      const p = String(flat.reflector1).trim().split(/\s+/);
      set('dstar_reflector', p[0]);
      set('dstar_module', (p[1] || 'B').toUpperCase());
      const md = $id('spDstarMod'); if (md && p[1]) md.value = p[1].toUpperCase();
      const s2 = $id('spDstarSel'); if (s2) s2.textContent = String(flat.reflector1).trim();
    }
    const su = $id('spDstarStartup');
    if (su && flat.atStartup1 != null) su.value = String(flat.atStartup1) === '1' ? '1' : '0';
    await fetchDstarFlags();
  } else if (mode === 'FM') {
    set('callsign', flat.Callsign); set('ctcss', flat.CTCSSFrequency);
  } else if (mode === 'POCSAG') {
    set('freq', flat.Frequency);
  }
}
/* ==================== ★ v2 智能选择器（DMR 主机 / D-STAR 反射器） ==================== */
const SP = {
  dmr:   { url: '/api/bm-list',          net: 'BM', q: '', rows: null, cur: '', max: 0 },
  ysf:   { url: '/api/ysf-hosts',        net: '', q: '', rows: null, cur: '', max: 0 },
  dstar: { url: '/api/dstar-reflectors', net: '', q: '', rows: null, cur: '', max: 0 },
};
const SP_IDS = { dmr: 'spDmr', ysf: 'spYsf', dstar: 'spDstar' };

function spEl(t, suf) { return document.getElementById(SP_IDS[t] + suf); }

function spEsc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function spEnsure(t) {
  const S = SP[t];
  if (S.rows) return S.rows;
  const r = await fetch(S.url, { credentials: 'same-origin' });
  const d = await r.json();
  if (t === 'dmr') {
    S.rows = (d.masters || []).map(m => ({
      key: m.host, typ: (m.net || 'BM').toUpperCase(),
      name: m.label || ((m.net || 'BM') + (m.id || '')),
      ip: m.host, port: m.port, pw: m.pw || '', country: m.country || '',
    }));
  } else if (t === 'ysf') {
    /* YSF / WIRES-X 房间：key=房间号（写入 YSFGateway 的 Startup），名称带编号便于搜索辨认 */
    S.rows = (d.items || []).map(y => {
      const id = String(y.id || '');
      const nm = String(y.name || '');
      const ds = String(y.desc || '');
      return {
        key: id, typ: 'YSF', raw: nm,
        name: (id ? id + ' · ' : '') + nm + (ds ? ('（' + ds + '）') : ''),
        ip: y.host || '', port: y.port || '', pw: '', country: '',
      };
    });
    S.rows.sort((a, b) => String(a.key).localeCompare(String(b.key)));
  } else {
    const out = [];
    ['REF', 'XRF', 'DCS', 'CCS', 'XLX', 'FCS'].forEach(k => {
      (d[k] || []).forEach(x => out.push({
        key: x.name, typ: k, name: x.name, ip: x.ip || '', port: '', pw: '',
      }));
    });
    out.sort((a, b) => a.typ.localeCompare(b.typ) || a.name.localeCompare(b.name));
    S.rows = out;
  }
  return S.rows;
}

function spFiltered(t) {
  const S = SP[t];
  if (!S.rows) return [];
  const q = S.q.trim().toLowerCase();
  return S.rows.filter(x => {
    if (S.net && x.typ !== S.net) return false;
    if (!q) return true;
    return (x.name + ' ' + x.ip + ' ' + x.typ + ' ' + (x.country || '') + ' '
            + (x.key || '') + ' ' + (x.raw || '')).toLowerCase().includes(q);
  });
}

function spRenderChips(t) {
  const S = SP[t], box = spEl(t, 'Chips');
  if (!box || !S.rows) return;
  const cnt = {};
  S.rows.forEach(x => { cnt[x.typ] = (cnt[x.typ] || 0) + 1; });
  const keys = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]);
  let h = '<span class="sp-chip' + (S.net === '' ? ' on' : '') + '" data-net="">全部<b>'
        + S.rows.length + '</b></span>';
  keys.forEach(k => {
    h += '<span class="sp-chip' + (S.net === k ? ' on' : '') + '" data-net="' + spEsc(k) + '">'
       + spEsc(k) + '<b>' + cnt[k] + '</b></span>';
  });
  box.innerHTML = h;
  box.querySelectorAll('.sp-chip').forEach(c => {
    c.addEventListener('click', () => {
      S.net = c.dataset.net || ''; spRenderChips(t); spRenderList(t);
    });
  });
}

function spRenderList(t) {
  const S = SP[t], box = spEl(t, 'List'), cntEl = spEl(t, 'Cnt');
  if (!box) return;
  const rows = spFiltered(t);
  if (cntEl) cntEl.textContent = rows.length + ' / ' + (S.rows ? S.rows.length : 0) + ' 个';
  if (!rows.length) {
    box.innerHTML = '<div class="sp-empty">没有匹配项 —— 试试关键字 '
      + (t === 'dmr' ? 'BM / HB / China / 4602'
                     : (t === 'ysf' ? '46087 / CN / China / XLX / JP'
                                    : 'REF / XRF / DCS / XLX / FCS')) + '</div>';
    return;
  }
  box.innerHTML = (S.max ? rows.slice(0, S.max) : rows).map(x => {
    const on = (x.key === S.cur) ? ' on' : '';
    const right = x.port ? ('<span class="pt">:' + spEsc(x.port) + '</span>') : '';
    return '<div class="sp-row' + on + '" data-key="' + spEsc(x.key) + '">'
      + '<span class="nm">' + spEsc(x.name) + '</span>'
      + '<span class="nt">' + spEsc(x.typ) + '</span>'
      + '<span class="hs">' + spEsc(x.ip) + '</span>' + right + '</div>';
  }).join('') + (S.max && rows.length > S.max
      ? '<div class="sp-empty">… 还有 ' + (rows.length - S.max) + ' 条，请继续输入关键字缩小范围</div>'
      : '');
  if (!box._spBound) {
    box.addEventListener('click', function(e) {
      var row = e.target.closest('.sp-row');
      if (row && row.dataset.key) spPick(t, row.dataset.key);
    });
    box._spBound = true;
  }
}

function spFind(t, key) { return (SP[t].rows || []).find(x => x.key === key); }

function spPick(t, key) {
  const S = SP[t], item = spFind(t, key);
  if (!item) return;
  S.cur = key;
  const sel = spEl(t, 'Sel');
  if (sel) sel.textContent = item.name + (item.port ? (':' + item.port) : '') + '  →  ' + item.ip;
  if (t === 'dmr') {
    const h = $id('dmrBmHost'); if (h) h.value = item.ip;
    const p = document.querySelector('#form-dmr [name="bm_port"]');
    if (p && item.port) p.value = item.port;
    const pw = document.querySelector('#form-dmr [name="bm_pw"]');
    if (pw && item.pw) pw.value = item.pw;
  } else if (t === 'ysf') {
    /* ★ 本机 YSFGateway 的 Startup 是〈反射器名称〉（官方示例 Startup=Alabama-Link），
       不是房间号；所以这里填 Pi-Star 的完整名（如 CN-YSF-TG46020）才能连上。 */
    const h = $id('ysfStartup'); if (h) h.value = (item.raw || item.key);
    const src = spEl('ysf', 'Src');
    if (src) src.textContent = '已选 ' + item.name + ' → ' + (item.ip || '')
      + '（写入 ' + (item.raw || item.key) + '）';
  } else {
    const h = $id('dstar_reflector'); if (h) h.value = item.name;
    const hr = $id('dstar_reflector_full');
    const mod = ($id('spDstarMod') || {}).value || 'C';
    if (hr) hr.value = item.name + ' ' + mod;
    const src = spEl('dstar', 'Src');
    if (src) src.textContent = '已选 ' + item.name + ' → ' + (item.ip || '');
  }
  spRenderList(t);
  if (typeof toast === 'function') toast('已选择 ' + item.name, 'ok');
}

async function spLoad(t, force) {
  const S = SP[t];
  if (force) S.rows = null;
  try {
    await spEnsure(t);
    spRenderChips(t);
    spRenderList(t);
  } catch (e) {
    const box = spEl(t, 'List');
    if (box) box.innerHTML = '<div class="sp-empty">主机库读取失败：' + spEsc(e.message) + '</div>';
  }
}

function spRefresh(t) { spLoad(t, true); }

async function spLoadCurrent(t) {
  try {
    const mode = (t === 'dmr') ? 'DMR' : (t === 'ysf' ? 'YSF' : 'D-STAR');
    const r = await fetch('/api/config/' + mode, { credentials: 'same-origin' });
    const data = await r.json();
    const flat = {};
    Object.keys(data).forEach(sec => Object.assign(flat, data[sec]));
    if (t === 'dmr') {
      /* [FIX 1.0.86] 同上：9 位网关 Id 必须优先于 7 位主机 Id，否则 ESSID 勾选状态会丢 */
      const _gwId = String(((data.DMRGATEWAY || {}).Id) || ((data.DMR_NETWORK || {}).Id) || '').replace(/[^0-9]/g, '');
      const _hostId = String((data.MMDVM_HOST || {}).Id || '').replace(/[^0-9]/g, '');
      if (_gwId) flat.Id = _gwId; else if (_hostId) flat.Id = _hostId;
    }
    await spLoad(t);
    if (t === 'dmr') {
      const host = flat.Address || '';
      const pw = document.querySelector('#form-dmr [name="bm_pw"]');
      if (pw && flat.Password) pw.value = flat.Password;
      const pt = document.querySelector('#form-dmr [name="bm_port"]');
      if (pt && flat.Port) pt.value = flat.Port;
      /* 把已保存的 Id 拆显为 7 位 DMR ID + 2 位 ESSID（并同步开关状态） */
      if (typeof window.loadEssidFromConfig === 'function') window.loadEssidFromConfig(flat);
      if (host) {
        const it = (SP.dmr.rows || []).find(x => x.ip === host);
        if (it) { spPick('dmr', it.key); }
        else {
          const h = $id('dmrBmHost'); if (h) h.value = host;
          const s = spEl('dmr', 'Sel'); if (s) s.textContent = host + '（库中未列出）';
        }
      }
    } else if (t === 'ysf') {
      const st = String((flat.Startup != null ? flat.Startup : flat.startup) || '').trim();
      if (st) {
        const it = (SP.ysf.rows || []).find(x => String(x.key) === st || String(x.raw) === st);
        if (it) { spPick('ysf', it.key); }
        else {
          const h = $id('ysfStartup'); if (h) h.value = st;
          const s = spEl('ysf', 'Sel'); if (s) s.textContent = st + '（库中未列出）';
        }
      }
    } else {
      const refl = (flat.reflector1 || '').trim();
      const su = $id('spDstarStartup');
      if (su) su.value = String(flat.atStartup1 || '1') === '1' ? '1' : '0';
      if (refl) {
        const p2 = refl.split(/\s+/);
        const mod = $id('spDstarMod');
        if (mod && p2[1]) mod.value = p2[1].toUpperCase();
        const it = (SP.dstar.rows || []).find(x => x.name === p2[0]);
        if (it) { spPick('dstar', it.key); }
        else {
          const s = spEl('dstar', 'Sel'); if (s) s.textContent = refl + '（库中未列出）';
          const h = $id('dstar_reflector'); if (h) h.value = p2[0];
        }
      }
    }
  } catch (e) { /* 忽略 */ }
}

function spBind(t) {
  const inp = spEl(t, 'In');
  if (inp) {
    var _spTimer = null;
    inp.addEventListener('input', () => {
      SP[t].q = inp.value;
      if (!SP[t].rows) { spLoad(t); return; }
      clearTimeout(_spTimer);
      _spTimer = setTimeout(() => spRenderList(t), 120);
    });
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const rows = spFiltered(t);
        if (rows.length) spPick(t, rows[0].key);
      }
    });
  }
  const mod = spEl(t, 'Mod');
  if (mod) {
    mod.addEventListener('change', () => {
      const hr = $id('dstar_reflector_full');
      const cf = $id('dstar_reflector');
      if (hr && cf && cf.value) hr.value = cf.value + ' ' + mod.value;
    });
  }
  const st = spEl(t, 'Startup');
  if (st) {
    st.addEventListener('change', () => {
      const hs = $id('dstar_atstartup');
      if (hs) hs.value = st.value;
      document.querySelectorAll('input[name="dstar_startup"]').forEach(r => {
        r.checked = (r.value === st.value);
      });
    });
  }
  spLoad(t);
}
spBind('dmr');
spBind('ysf');
spBind('dstar');
fillHostPresets();
prefill('DMR');

/* ESSID 开关：取消勾选 -> 禁用输入框（disabled 的控件不进入 FormData，等于不启用） */
(function () {
  var cb = document.getElementById('essidOn'), iv = document.getElementById('essidVal');
  if (!cb || !iv) return;
  var sync = function () {
    iv.disabled = !cb.checked;
    iv.style.opacity = cb.checked ? '1' : '.45';
  };
  cb.addEventListener('change', sync);
  sync();
  /* 从现有配置回填：Id 为 9 位时拆成 7 位 + 2 位 ESSID */
  window.loadEssidFromConfig = function (flat) {
    var v = String(flat && flat.Id ? flat.Id : '').replace(/[^0-9]/g, '');
    if (v.length >= 9) {
      var base = v.slice(0, 7), es = v.slice(-2);
      var idEl = document.querySelector('#form-dmr [name="id"]');
      if (idEl) idEl.value = base;
      cb.checked = true;
      iv.value = /^(0[1-9]|[1-9][0-9])$/.test(es) ? es : '01';
      iv.disabled = false;
      iv.style.opacity = '1';
    }
  };
})();

window.spRefresh = spRefresh;
window.spLoadCurrent = spLoadCurrent;

function loadBmList(force) { spLoad('dmr', !!force); }
function fillDstarSel() { return spLoad('dstar'); }
window.loadBmList = loadBmList;
window.fillDstarSel = fillDstarSel;

/* 从 Pi-Star 重建主机库（本地 .txt -> JSON） */
async function rebuildHosts() {
  if (!confirm('将从 /usr/local/etc/hosts 的主机文件重建全部 JSON 索引？')) return;
  toast('正在重建主机库…');
  try {
    const r = await fetch('/api/hosts-rebuild', { method: 'POST' });
    const d = await r.json();
    toast(d.ok ? '主机库已重建' : ('重建失败: ' + (d.message || '')), d.ok ? 'ok' : 'error');
    await spLoad('dmr', true);
    await spLoad('dstar', true);
  } catch (e) {
    toast('重建失败: ' + e.message, 'error');
  }
}
window.rebuildHosts = rebuildHosts;

/* ==================== 系统设置（WiFi / 系统信息 / 电源）==================== */
function _sysEsc(s){
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
async function sysLoad(){
  const info = $id('sysInfo'), net = $id('sysNet');
  try{
    const d = await (await fetch('/api/sys/info', {credentials:'same-origin'})).json();
    if(info){
      // ★ 简洁版：设备 / 时间 / 运行 / 状态 共 4 行
      const mdl = String(d.MODEL || '-')
        .replace(/^Raspberry Pi\s*/i, 'Pi ').replace(/\s*Model\s*/i, ' ').replace(/\s*Rev.*$/i, '').trim();
      info.innerHTML =
        '<span class="kvw">ARCH 版本</span><b style="font-size:18px;color:#86efac">ARCH ' + _sysEsc(d.ARCH_VERSION || '-') + '</b>　' + _sysEsc(d.ARCH_CHANNEL || '-') + '<br>'
        + '<span class="kvw">固件版本</span><b>' + _sysEsc(d.FIRMWARE_VERSION || '-') + '</b><br>'
        + '<span class="kvw">\u8bbe\u5907</span><b>' + _sysEsc(d.HOSTNAME || '-') + '</b>　' + _sysEsc(mdl || '-') + '<br>'
        + '<span class="kvw">\u65f6\u95f4</span><b>' + _sysEsc(d.TIME || '-') + '</b>　' + _sysEsc(d.TZ || '-') + '<br>'
        + '<span class="kvw">\u8fd0\u884c</span>' + _sysEsc(d.UPTIME || '-') + '<br>'
        + '<span class="kvw">\u72b6\u6001</span>\u6e29\u5ea6 <b>' + _sysEsc(d.TEMP || '-') + '</b>　\u5185\u5b58 '
        + _sysEsc(d.MEM || '-') + '　\u78c1\u76d8 ' + _sysEsc(d.DISK || '-');
    }
  }catch(e){ if(info) info.textContent = '系统信息读取失败：' + e; }
  try{
    const t = await (await fetch('/api/sys/net', {credentials:'same-origin'})).text();
    if(net) net.innerHTML = _netRender(t);
  }catch(e){ if(net) net.textContent = '网络信息读取失败：' + e; }

  /* ★ 网络状态：把后端的键值渲染成简洁的 3 行 */
  function _netRender(txt){
    const g = function(k){ const m = new RegExp('^' + k + '=(.*)$', 'm').exec(txt || ''); return m ? m[1].trim() : ''; };
    const one = function(lbl, pre, icon){
      if(!g(pre + '_TYPE')) return '';
      const st = g(pre + '_STATE'), ip = g(pre + '_IP'), gw = g(pre + '_GW');
      const ok = (st === 'connected');
      let val = (ok && ip && ip !== '-') ? ip : '未连接';
      let meta = '';
      if(ok && gw && gw !== '-') meta += '<span class="netmeta-k">网关</span>' + _sysEsc(gw);
      if(pre === 'WLAN0' && g('WIFI_SSID') && g('WIFI_SSID') !== '-'){
        meta += (meta ? '<span class="netsep">·</span>' : '') + '<span class="netmeta-k">SSID</span>' + _sysEsc(g('WIFI_SSID'));
      }
      return '<div class="netrow">'
        + '<span class="netico">' + icon + '</span>'
        + '<span class="netlab">' + lbl + '</span>'
        + '<span class="netdot ' + (ok ? 'ok' : 'off') + '"></span>'
        + '<span class="netval' + (ok ? '' : ' off') + '">' + _sysEsc(val) + '</span>'
        + (meta ? '<span class="netmeta">' + meta + '</span>' : '')
        + '</div>';
    };
    const dns = (g('DNS') || '').split(',')
      .filter(function(x){ return x && x.indexOf('fe80:') !== 0 && x.indexOf('%') < 0; });
    var extra = '';
    if(g('USBETH_NAME')){
      extra = one(g('USBETH_NAME') || 'USB/4G', 'USBETH', '📶');
    }
    return one('有线', 'ETH0', '🔌')
         + one('无线', 'WLAN0', '📶')
         + extra
         + '<div class="netrow"><span class="netico">🌐</span><span class="netlab">DNS</span>'
         + '<span class="netdot netdot-none"></span><span class="netval mono">'
         + _sysEsc(dns.length ? dns.join('  \u00b7  ') : '-') + '</span></div>';
  }
  try{ if(typeof sysTimeLoad === 'function') sysTimeLoad(); }catch(e){ console.warn('sysTimeLoad', e); }
  try{ if(typeof wifiStatusShow === 'function') wifiStatusShow(); }catch(e){ console.warn('wifiStatusShow', e); }
  try{ if(typeof aptStatLoad === 'function') aptStatLoad(); }catch(e){ console.warn('aptStatLoad', e); }
  try{ if(typeof fwInfoLoad === 'function') fwInfoLoad(); }catch(e){ console.warn('fwInfoLoad', e); }
  try{
    const _sec = $id('page-sys');
    if(window.__repErr) window.__repErr('sys', 'secKids=' + (_sec ? _sec.children.length : -1)
      + ' infoLen=' + (($id('sysInfo') || {}).innerHTML || '').length
      + ' wifiLen=' + (($id('wifiStatus') || {}).innerHTML || '').length
      + ' aptLen=' + (($id('aptStat') || {}).innerHTML || '').length
      + ' fwLen=' + (($id('fwStat') || {}).innerHTML || '').length, location.href, '', '');
  }catch(e2){}
}
/* ---------- WiFi（双后端：NetworkManager / wpa_supplicant）---------- */
function _wifiFill(ssid){ const el = $id('wifiSsid'); if(el) el.value = ssid; }
function _bandTag(b){
  const c = (b === '5G') ? '#a78bfa' : ((b === '2.4G') ? '#34d399' : 'var(--dim)');
  return '<span style="color:' + c + ';font-size:12px;font-weight:700;min-width:38px">' + _sysEsc(b || '-') + '</span>';
}
function _sigBar(n){
  n = Number(n) || 0;
  const c = n >= 70 ? '#34d399' : (n >= 45 ? '#fbbf24' : '#f87171');
  return '<span style="display:inline-block;min-width:104px"><span style="display:inline-block;height:7px;width:'
    + Math.max(6, Math.min(100, n)) + '%;background:' + c + ';border-radius:4px;vertical-align:middle"></span> '
    + n + '%</span>';
}
async function wifiStatusShow(){
  const box = $id('wifiStatus'), badge = $id('wifiBackend');
  const d = await _sysAct('wifi-status', []);
  const t = d.message || '';
  const g = function(k){ const m = new RegExp('^' + k + '=(.*)$', 'm').exec(t); return m ? m[1].trim() : ''; };
  const be = g('BACKEND'), radio = g('RADIO');
  if(badge) badge.textContent = (be === 'nm') ? 'NetworkManager' : ((be === 'wpa') ? 'wpa_supplicant' : (be || 'WIFI'));
  const rb = $id('wifiRadioBtn');
  if(rb) rb.textContent = (radio === 'disabled') ? '📶 打开 WiFi' : '📴 关闭 WiFi';
  let cc = '';
  if(box){
    const ssid = g('SSID'), ip = g('IP'), sig = g('SIGNAL'), band = g('BAND'), freq = g('FREQ'),
          rate = g('RATE'), sec = g('WIFISEC') || g('KEYMGMT'), gw = g('GW'),
          saved = g('SAVED'), bands = g('BANDS'), iface = g('IFACE');
    cc = g('COUNTRY');
    box.innerHTML =
      '状态：' + ((radio === 'disabled') ? '<b style="color:#fbbf24">射频已关闭</b>'
                : (ssid ? '<b style="color:#34d399">已连接</b>' : '<span style="color:#fbbf24">未连接（网线仍可用）</span>'))
      + '　SSID：<b>' + _sysEsc(ssid || '-') + '</b>'
      + (band ? '　频段：' + _bandTag(band) : '')
      + (freq && freq !== '-' ? '　频率：' + _sysEsc(freq) : '')
      + (sig ? '　信号：' + _sysEsc(sig) : '')
      + (rate ? '　速率：' + _sysEsc(rate) : '') + '<br>'
      + 'IP：<b>' + _sysEsc(ip || '-') + '</b>　网关：' + _sysEsc(gw || '-')
      + '　加密：' + _sysEsc(sec || '-')
      + (bands !== '' ? '　信道数：' + _sysEsc(bands) : '')
      + '　已保存网络：' + _sysEsc(saved || '0')
      + (iface && iface !== '-' ? '　网卡：' + _sysEsc(iface) : '');
  }
  try{ if(window.__repErr) window.__repErr('wifi', 'statusLen=' + (($id('wifiStatus') || {}).innerHTML || '').length
        + ' scanLen=' + (($id('wifiList') || {}).innerHTML || '').length, location.href, '', ''); }catch(e){}
}
async function wifiScan(){
  const box = $id('wifiList'), msg = $id('wifiMsg');
  if(msg) msg.textContent = '扫描中…（约 8-15 秒）';
  if(box) box.innerHTML = '正在扫描附近 WiFi…';
  const d = await _sysAct('wifi-scan', []);
  const lines = (d.message || '').split('\n').filter(function(x){ return x.indexOf('|') >= 0; });
  if(!box) return;
  if(!lines.length){
    box.innerHTML = _sysEsc(String(d.message || '').trim() || '未发现无线网络');
    if(msg) msg.textContent = '';
    return;
  }
  box.innerHTML = lines.map(function(ln){
    const f = ln.split('|');
    const use = String(f[0] || '').trim() === '*';
    const ssid = String(f[1] || '').trim(), sig = String(f[2] || '').trim(), sec = String(f[3] || '').trim(),
          ch = String(f[4] || '').trim(), fq = String(f[5] || '').trim(), band = String(f[6] || '').trim();
    const safe = ssid.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    return '<div style="display:flex;gap:8px;align-items:center;border-bottom:1px solid var(--border);padding:4px 2px;flex-wrap:wrap">'
      + '<span style="min-width:14px">' + (use ? '✅' : '') + '</span>'
      + '<b style="min-width:160px;color:#fff">' + _sysEsc(ssid) + '</b>'
      + _bandTag(band) + _sigBar(sig)
      + '<span style="color:var(--muted);min-width:110px">' + _sysEsc(sec || '开放') + '</span>'
      + '<span style="color:var(--dim)">信道 ' + _sysEsc(ch) + ((fq && fq !== '-') ? (' / ' + _sysEsc(fq) + ' MHz') : '') + '</span>'
      + '<a href="#" style="margin-left:auto" onclick="_wifiFill(\'' + safe + '\');return false;">用它 →</a></div>';
  }).join('');
  if(msg) msg.textContent = '共 ' + lines.length + ' 个网络（按信号强弱排序）';
}
async function wifiSaved(){
  const box = $id('wifiSavedBox'), msg = $id('wifiMsg');
  if(msg) msg.textContent = '读取已保存网络…';
  const d = await _sysAct('wifi-saved', []);
  const lines = (d.message || '').split('\n').filter(function(x){ return x.indexOf('|') >= 0; });
  if(!box) return;
  if(!lines.length){
    box.innerHTML = '';
    if(msg) msg.textContent = '';
    return;
  }
  box.innerHTML = '<div class="hint" style="margin-bottom:6px">已保存的 WiFi（重启后按“自动连接 / 优先级”选择接入）</div>'
    + lines.map(function(ln){
        const f = ln.split('|');
        const name = String(f[0] || '').trim(), ssid = String(f[1] || '').trim(),
              ac = String(f[2] || '').trim(), pr = String(f[3] || '').trim(), km = String(f[4] || '').trim();
        const n1 = name.replace(/'/g, "\\'"), s1 = ssid.replace(/'/g, "\\'");
        return '<div style="display:flex;gap:8px;align-items:center;border-bottom:1px solid var(--border);padding:5px 2px;flex-wrap:wrap">'
          + '<b style="min-width:140px;color:#fff">' + _sysEsc(ssid) + '</b>'
          + '<span class="hint" style="min-width:170px">连接名：' + _sysEsc(name) + '</span>'
          + '<span class="hint">' + _sysEsc(km || '-') + '　优先级 ' + _sysEsc(pr || '0') + '　自动连接：<b>' + (ac === 'yes' ? '开' : '关') + '</b></span>'
          + '<span style="margin-left:auto;display:flex;gap:6px;flex-wrap:wrap">'
          + '<button class="btn btn-sm" onclick="wifiReconnect(\'' + n1 + '\')">连接</button>'
          + '<button class="btn btn-sm" onclick="wifiAutoconnect(\'' + n1 + '\',\'' + (ac === 'yes' ? 'no' : 'yes') + '\')">' + (ac === 'yes' ? '关自启' : '开自启') + '</button>'
          + '<button class="btn btn-sm" onclick="wifiPriority(\'' + n1 + '\')">优先级</button>'
          + '<button class="btn btn-sm" style="background:#7f1d1d;color:#fee2e2" onclick="wifiForget(\'' + n1 + '\',\'' + s1 + '\')">忘记</button>'
          + '</span></div>';
      }).join('');
  if(msg) msg.textContent = '共 ' + lines.length + ' 个已保存网络';
}
function sysUse(ssid){ _wifiFill(ssid); }
async function wifiConnect(){
  const ssid = (($id('wifiSsid') || {}).value || '').trim();
  const pw = (($id('wifiPw') || {}).value || '');
  const hb = $id('wifiHidden');
  const hid = (hb && hb.checked) ? '1' : '0';
  if(!ssid){ toast('请先填写 SSID', 'error'); return; }
  if(!confirm('连接 WiFi「' + ssid + '」？\n设备将在约 5 秒后切换 WiFi。ARCH多模网关热点会断开，请准备把手机也连接到「' + ssid + '」。\n切换后请访问 http://ARCH.local 或路由器给设备分配的新 IP。')) return;
  const m = $id('wifiConnMsg');
  if(m) m.textContent = '正在下发切换任务…';
  const fd = new FormData();
  fd.append('ssid', ssid); fd.append('password', pw); fd.append('hidden', hid);
  try{
    const d = await (await fetch('/api/sys/wifi/connect', {method:'POST', body:fd, credentials:'same-origin'})).json();
    if(m) m.textContent = d.message || '';
    toast(d.ok ? ('切换任务已下发：' + ssid) : ('失败：' + (d.message || '')), d.ok ? 'ok' : 'error');
    if(d.ok) setTimeout(function(){ m && (m.textContent = '设备切换中。若本页断开，请重新连接 WiFi 后访问 ARCH.local。'); }, 3000);
  }catch(e){ if(m) m.textContent = '请求失败：' + e; toast('连接失败：' + e, 'error'); }
}
async function wifiDisconnect(){
  if(!confirm('断开当前 WiFi？（若你正通过 WiFi 访问本页面会立即断线）')) return;
  const d = await _sysAct('wifi-disconnect', []);
  toast(d.message || '已断开', d.ok ? 'ok' : 'error');
  setTimeout(wifiStatusShow, 2000);
}
async function wifiRadioToggle(){
  const rb = $id('wifiRadioBtn');
  const isOff = rb && /打开/.test(rb.textContent);
  if(!isOff && !confirm('关闭 WiFi 射频？（之后只能通过网线访问本页面）')) return;
  const d = await _sysAct('wifi-radio', [isOff ? 'on' : 'off']);
  toast(d.message || '已执行', d.ok ? 'ok' : 'error');
  setTimeout(wifiStatusShow, 2500);
}
async function wifiForget(name, ssid){
  if(!confirm('忘记 WiFi「' + (ssid || name) + '」？\n删除已保存的配置（当前连接会保留到重启）')) return;
  const d = await _sysAct('wifi-forget', [name]);
  toast(d.message || '已删除', d.ok ? 'ok' : 'error');
  setTimeout(function(){ wifiSaved(); wifiStatusShow(); }, 1500);
}
async function wifiReconnect(name){
  toast('正在连接 ' + name + ' …');
  const d = await _sysAct('wifi-reconnect', [name]);
  toast(d.message || '已下发', d.ok ? 'ok' : 'error');
  setTimeout(wifiStatusShow, 3000);
}
async function wifiAutoconnect(name, v){
  const d = await _sysAct('wifi-autoconnect', [name, v]);
  toast(d.message || '已设置', d.ok ? 'ok' : 'error');
  setTimeout(wifiSaved, 1200);
}
async function wifiPriority(name){
  const v = prompt('设置「' + name + '」自动连接优先级（数字越大越优先，如 10）', '10');
  if(v === null) return;
  const d = await _sysAct('wifi-priority', [name, String(v).trim()]);
  toast(d.message || '已设置', d.ok ? 'ok' : 'error');
  setTimeout(wifiSaved, 1200);
}
function sysWifiConnect(){ wifiConnect(); }
function sysWifiDisconnect(){ wifiDisconnect(); }
async function sysAction(a){
  if(a === 'reboot' && !confirm('确定重启设备？')) return;
  if(a === 'shutdown' && !confirm('确定关机？需要手动上电才能恢复。')) return;
  if(a === 'restart-hotspot' && !confirm('重启全部热点服务（MMDVM-Multi + 各通道 + 网关，约 30 秒）？')) return;
  toast('正在执行…');
  try{
    const d = await (await fetch('/api/sys/action', {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:a})})).json();
    toast(d.message || (d.ok ? '已执行' : '执行失败'), d.ok ? 'ok' : 'error');
  }catch(e){ toast('执行失败：' + e, 'error'); }
}
/* ==================== 树莓派系统更新（apt）==================== */
async function factoryReset(){
  var cb = document.getElementById('frNet');
  var net = !!(cb && cb.checked);
  if(!confirm('确定要恢复出厂设置吗？将清除呼号/DMR ID、信道规划、各模式参数与网关登录、FM 哑音与尾音、FMO 证书；保留固件、设备ID、远程管理与 WiFi/网络设置。当前配置会先自动备份。')) return;
  if(!confirm('再次确认：恢复后需要重新配置呼号、频率与各模式参数。继续执行？')) return;
  var msg = document.getElementById('frMsg'); if(msg) msg.textContent = '正在恢复出厂设置…';
  toast('正在恢复出厂设置…');
  try{
    const d = await (await fetch('/api/sys/action', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'factory-reset', args:[net?'net':'']})})).json();
    toast(d.message || (d.ok ? '已恢复出厂设置' : '恢复失败'), d.ok ? 'ok' : 'error');
    if(msg) msg.textContent = d.message || '';
  }catch(e){ toast('恢复失败：' + e, 'error'); if(msg) msg.textContent = ''; }
}
function _aptOut(t){
  const o = $id('aptOut');
  if(!o) return;
  o.textContent = t || '';
  o.style.display = 'block';
  o.scrollTop = o.scrollHeight;
}
async function aptStatLoad(){
  const box = $id('aptStat');
  const d = await _sysAct('sys-upgrade-count', []);
  const t = d.message || '';
  const m = /^UPGRADABLE=(\d+)/m.exec(t);
  const cnt = m ? Number(m[1]) : -1;
  if(!box) return;
  const inst = t.split('\n').filter(function(x){ return /^\s*Inst /.test(x); }).slice(0, 6)
    .map(function(x){ return x.trim().split(' ')[1]; }).join('、');
  box.innerHTML = '可升级软件包：<b style="color:' + ((cnt > 0) ? '#fbbf24' : '#34d399') + '">'
    + (cnt >= 0 ? cnt : '?') + '</b> 个'
    + ((cnt === 0) ? '（系统已是最新）' : '');
  try{ if(window.__repErr) window.__repErr('apt', 'len=' + (box.innerHTML || '').length + ' text=' + (box.textContent || '').slice(0, 60), location.href, '', ''); }catch(e){}
}
async function aptCheck(){
  const msg = $id('aptMsg');
  if(msg) msg.textContent = '正在 apt-get update …（10-60 秒，请稍等）';
  const d = await _sysAct('sys-update-check', []);
  _aptOut(d.message || '(无输出)');
  if(msg) msg.textContent = '检查完成';
  aptStatLoad();
}
var _UPD_TIMERS={},_UPD_META={};
function _updProg(kind,pct,text,color){
  var w=document.getElementById(kind+'ProgWrap'),b=document.getElementById(kind+'ProgBar'),x=document.getElementById(kind+'ProgText');
  if(w)w.style.display='block'; if(x)x.style.display='block';
  if(b){b.style.width=Math.max(0,Math.min(100,Number(pct)||0))+'%';b.style.background=color||'#60a5fa';}
  if(x)x.textContent=text||'';
}
async function _updPoll(kind,action){
  try{
    var d=await _sysAct(action,[]),t=d.message||'';
    var m=/^STATE=(\w+)/m.exec(t),st=m?m[1]:'';
    var el=(Date.now()-_UPD_META[kind].start)/1000;
    if(st==='running')_UPD_META[kind].seen=true;
    /* 进度：渐近到 99%，不再硬顶 94%（大包+安装慢时会长时间卡在 94%） */
    var _cap=99, _tau=(kind==='fw'?90:900);
    var p=Math.max(5,Math.min(_cap,Math.round(_cap*(1-Math.exp(-el/_tau)))));
    if(kind==='fw'&&p>94)p=94+Math.round((p-94)*0.99);
    if(kind==='sys'){
      if(/Reading package lists|apt-get update/i.test(t))p=Math.max(p,20);
      if(/Unpacking /i.test(t))p=Math.max(p,65);
      if(/Setting up /i.test(t))p=Math.max(p,88);
      if(/系统更新完成/.test(t))p=100;
    }else{
      if(/current\.json/.test(t))p=Math.max(p,10);
      if(/下载|download|curl/.test(t))p=Math.max(p,25);
      if(/sha256|校验|OK/.test(t))p=Math.max(p,42);
      if(/tar|解包|extract/.test(t))p=Math.max(p,55);
      if(/install|安装|快速补丁/.test(t))p=Math.max(p,72);
      if(/restart|重启/.test(t))p=Math.max(p,90);
    }
    if(st==='running')_updProg(kind,p,'更新执行中… '+p+'%　已用 '+Math.round(el)+' 秒','#60a5fa');
    else if(_UPD_META[kind].seen||p>=100){
      clearInterval(_UPD_TIMERS[kind]);_UPD_TIMERS[kind]=null;
      _updProg(kind,100,'更新完成，请重启设备','#34d399');
      if(kind==='fw')setTimeout(function(){location.reload();},5000);
    }else if(/已是最新版本/.test(t)){
      /* [1.0.153] 兜底：进度轮询里看到"已是最新版本"就给绿色结果，不再等到 20 秒报红 */
      clearInterval(_UPD_TIMERS[kind]);_UPD_TIMERS[kind]=null;
      var _lm=/已是最新版本\s*([0-9.]+)/.exec(t);
      _updProg(kind,100,'已是最新版本 '+(_lm?_lm[1]:'')+'，无需升级','#34d399');
    }else if(el>20){
      clearInterval(_UPD_TIMERS[kind]);_UPD_TIMERS[kind]=null;
      _updProg(kind,0,'未检测到更新任务，请查看日志','#f87171');
    }else _updProg(kind,p,'正在启动更新任务…','#60a5fa');
  }catch(e){clearInterval(_UPD_TIMERS[kind]);_UPD_TIMERS[kind]=null;_updProg(kind,0,'读取进度失败','#f87171');}
}
function _updStart(kind,action,text){
  _UPD_META[kind]={seen:false,start:Date.now()};
  _updProg(kind,5,text||'正在启动更新…','#60a5fa');
  if(_UPD_TIMERS[kind])clearInterval(_UPD_TIMERS[kind]);
  _UPD_TIMERS[kind]=setInterval(function(){_updPoll(kind,action)},2000);
  setTimeout(function(){_updPoll(kind,action)},800);
}
async function aptUpgrade(){
  if(!confirm('开始树莓派系统更新？\n· 后台执行，可以关闭网页\n· 通常 5-20 分钟\n· 更新期间热点服务照常工作\n· 完成后建议重启设备')) return;
  const d = await _sysAct('sys-update', []);
  toast(d.message || (d.ok ? '已开始树莓派系统更新' : '启动失败'), d.ok ? 'ok' : 'error');
  if(d.ok) _updStart('sys','sys-update-status','正在更新树莓派系统软件包…');
}
async function aptClean(){
  if(!confirm('清理无用依赖与软件源缓存？（安全操作，可释放磁盘）')) return;
  const d = await _sysAct('sys-update-clean', []);
  toast(d.message || '已开始', d.ok ? 'ok' : 'error');
  setTimeout(aptStatus, 2500);
}
async function aptStatus(){
  const d = await _sysAct('sys-update-status', []);
  _aptOut(d.message || '(无输出)');
  aptStatLoad();
}
async function remoteDebugStatus(){
  try{
    const d = await _sysAct('remote-debug', ['status']);
    const t = String((d&&d.message)||'');
    const on = /1/.test(t) && !/已关闭/.test(t);
    const b = $id('remoteDebugBadge');
    if(b){ b.textContent = on ? '已开启' : '已关闭';
      b.style.background = on ? 'rgba(16,185,129,.18)' : 'rgba(239,68,68,.18)';
      b.style.color = on ? '#34d399' : '#f87171'; }
    const m = $id('remoteDebugMsg'); if(m) m.textContent = t.replace(/\s+/g,' ').slice(0,120);
  }catch(e){}
}
async function remoteDebugCtl(act){
  const d = await _sysAct('remote-debug', [act]);
  toast((d&&d.message)||(act==='on'?'已开启':'已关闭'), (d&&d.ok)?'ok':'error');
  setTimeout(remoteDebugStatus, 800);
}
window.remoteDebugCtl = remoteDebugCtl; window.remoteDebugStatus = remoteDebugStatus;
window.aptStatLoad = aptStatLoad; window.aptCheck = aptCheck; window.aptUpgrade = aptUpgrade;
window.aptClean = aptClean; window.aptStatus = aptStatus;

/* ==================== ARCH固件更新 ==================== */
function _fwOut(t){
  const o = $id('fwOut');
  if(!o) return;
  o.textContent = t || '';
  o.style.display = 'block';
  o.scrollTop = o.scrollHeight;
}
function _kvGet(t, k){ const m = new RegExp('^' + k + '=(.*)$', 'm').exec(t || ''); return m ? m[1].trim() : ''; }
function _mb(n){ n = Number(n) || 0; return n > 0 ? ((n / 1048576).toFixed(2) + ' MB') : '-'; }
async function fwInfoLoad(){
  const box = $id('fwStat');
  if(!box) return;
  try{
    const d = await _sysAct('update-status', []);
    const t = d.message || '';
    const cur = _kvGet(t,'ARCH_CURRENT') || '-';
    const latest = _kvGet(t,'ARCH_LATEST');
    const up = _kvGet(t,'ARCH_UPDATE');
    let state;
    if(!latest){ state = '<b style="color:#fbbf24">无法读取最新版本（检查超时，请检查网络）</b>'; }
    else if(up === 'yes'){ state = '<b style="color:#fbbf24">可升级</b>'; }
    else { state = '<b style="color:#34d399">已是最新</b>'; }
    box.innerHTML = 'ARCH 当前版本：<b style="color:#86efac">' + _sysEsc(cur) + '</b>　最新版本：<b style="color:#86efac">' + _sysEsc(latest || '—') + '</b>　' + state;
  }catch(e){ box.textContent = '无法读取 ARCH 固件版本'; }
}

async function fwSaveUrl(){
  const u = (($id('fwUrl') || {}).value || '').trim();
  if(!/^https?:\/\//i.test(u)){ toast('地址必须以 http:// 或 https:// 开头', 'error'); return; }
  const d = await _sysAct('fw-url', [u]);
  toast(d.message || '已保存', d.ok ? 'ok' : 'error');
  fwInfoLoad();
}
async function fwCheck(){
  const msg = $id('fwMsg');
  if(msg) msg.textContent = '正在检查发布页…';
  const d = await _sysAct('fw-check', []);
  _fwOut(d.message || '(无输出)');
  if(msg) msg.textContent = '检查完成';
}
async function fwDownload(){
  const u = (($id('fwDirect') || {}).value || '').trim();
  if(!/^https?:\/\//i.test(u)){ toast('请先粘贴安装包直链', 'error'); return; }
  if(!confirm('从下面的地址下载安装包到设备 /tmp/mmdvm-fw.pkg ？\n' + u)) return;
  const d = await _sysAct('fw-download', [u]);
  toast(d.message || '已开始下载', d.ok ? 'ok' : 'error');
  setTimeout(fwLog, 5000);
}
async function fwInstallPkg(){
  if(!confirm('安装 /tmp/mmdvm-fw.pkg ？\n会先备份旧引擎，然后替换并自动重启 MMDVM-Multi')) return;
  const d = await _sysAct('fw-install', ['/tmp/mmdvm-fw.pkg']);
  toast(d.message || '已开始安装', d.ok ? 'ok' : 'error');
  setTimeout(fwLog, 5000);
}
async function fwUpload(){
  const inp = $id('fwFile');
  if(!inp || !inp.files || !inp.files.length){ toast('请先选择安装包文件', 'error'); return; }
  const f = inp.files[0];
  if(!confirm('上传安装包到设备？\n文件：' + f.name + '（' + _mb(f.size) + '）\n保存为 /tmp/mmdvm-fw.pkg，之后点「安装已下载/已上传的包」')) return;
  const msg = $id('fwMsg');
  if(msg) msg.textContent = '上传中…（大文件请耐心等待）';
  const fd = new FormData(); fd.append('file', f);
  try{
    const d = await (await fetch('/api/sys/fw/upload', {method:'POST', body:fd, credentials:'same-origin'})).json();
    if(msg) msg.textContent = d.message || '';
    toast(d.ok ? '上传完成' : ('上传失败：' + (d.message || '')), d.ok ? 'ok' : 'error');
  }catch(e){ if(msg) msg.textContent = '上传失败：' + e; toast('上传失败：' + e, 'error'); }
}
async function bootFwStatus(){
  const msg = $id('fwMsg');
  if(msg) msg.textContent = '读取引导固件状态…';
  const d = await _sysAct('boot-fw-status', []);
  _fwOut(d.message || '(无输出)');
  if(msg) msg.textContent = '';
}
async function bootFwUpdate(){
  if(!confirm('更新树莓派引导固件（EEPROM / VL805）？\n· 新固件下次重启后生效\n· 更新过程不要断电')) return;
  const d = await _sysAct('boot-fw-update', []);
  toast(d.message || '已开始', d.ok ? 'ok' : 'error');
  setTimeout(fwLog, 5000);
}
async function fwOnlineCheck(){
  const msg = $id('fwOnlineMsg');
  if(msg) msg.textContent = '正在读取在线固件清单…';
  const d = await _sysAct('fw-check', []);
  _fwOut(d.message || '(无输出)');
  const t = d.message || '';
  const up = _kvGet(t, 'UPDATE_AVAILABLE');
  const latest = _kvGet(t, 'LATEST_VERSION') || '-';
  if(msg) msg.textContent = '检查完成：在线版本 ' + latest + '，' + (up === 'yes' ? '可升级' : (up === 'no' ? '已是最新' : '状态未知'));
  fwInfoLoad();
}
async function fwOnlineUpgrade(){
  if(!confirm('开始在线更新 ARCH 固件？\n· 从 current.json 下载 ARCH 更新包\n· 自动校验 SHA-256\n· 后台执行，完成后建议重启设备')) return;
  const d = await _sysAct('hotspot-update', []);
  toast(d.message || (d.ok ? '已开始 ARCH 固件更新' : '启动失败'), d.ok ? 'ok' : 'error');
  if(!d.ok) return;
  /* [1.0.153] 已经是最新版时：直接给绿色结果，不再走进度条
     （原来会在 20 秒后显示红色"未检测到更新任务"，用户以为升级坏了） */
  await new Promise(function(r){ setTimeout(r, 1500); });
  try{
    const s = await _sysAct('hotspot-update-status', []);
    const t = String((s && s.message) || '');
    const m = /已是最新版本\s*([0-9.]+)/.exec(t);
    if(m){
      const msg = $id('fwOnlineMsg');
      if(msg) msg.textContent = '已是最新版本 ' + m[1] + '，无需升级';
      _updProg('fw', 100, '已是最新版本 ' + m[1] + '，无需升级', '#34d399');
      toast('已是最新版本 ' + m[1] + '，无需升级', 'ok');
      return;
    }
  }catch(e){}
  _updStart('fw','hotspot-update-status','正在下载并安装 ARCH 固件…');
}
async function hotspotOnlineUpgrade(){
  return aptUpgrade();
}
async function hotspotUpdateStatus(){
  return _updPoll('sys','sys-update-status');
}
async function fwLog(){
  const d = await _sysAct('fw-log', ['80']);
  _fwOut(d.message || '(暂无日志)');
  if(typeof fwInfoLoad === 'function' && $id('fwStat')) fwInfoLoad();
}
window.fwInfoLoad = fwInfoLoad; window.fwSaveUrl = fwSaveUrl; window.fwCheck = fwCheck;
window.fwDownload = fwDownload; window.fwInstallPkg = fwInstallPkg; window.fwUpload = fwUpload;
window.bootFwStatus = bootFwStatus; window.bootFwUpdate = bootFwUpdate; window.fwLog = fwLog;
window.hotspotOnlineUpgrade = hotspotOnlineUpgrade; window.hotspotUpdateStatus = hotspotUpdateStatus;

async function checkUpdates(){
  const el=$id('updateNotice');
  try{
    const d=await _sysAct('update-status', []);
    const t=d.message||'';
    const kv=function(k){const m=new RegExp('^'+k+'=(.*)$','m').exec(t); return m?m[1].trim():'';};
    const parts=[];
    if(kv('ARCH_UPDATE')==='yes') parts.push('ARCH 新版本 '+kv('ARCH_LATEST')+'（当前 '+kv('ARCH_CURRENT')+'）');
    
    let banner=$id('globalUpdateBanner');
    if(parts.length){
      const text='🔔 发现更新：'+parts.join('；')+'。请到系统设置执行更新。';
      if(el){el.style.display='block';el.textContent=text;}
      if(!banner){
        banner=document.createElement('div'); banner.id='globalUpdateBanner';
        banner.style.cssText='position:fixed;right:18px;bottom:18px;z-index:99999;max-width:min(460px,calc(100vw - 36px));padding:13px 42px 13px 15px;border:1px solid #f59e0b;border-radius:12px;background:#241b08;color:#fde68a;box-shadow:0 12px 36px rgba(0,0,0,.45);font-size:14px;line-height:1.55';
        const msg=document.createElement('span'); msg.id='globalUpdateText'; banner.appendChild(msg);
        const x=document.createElement('button'); x.type='button'; x.textContent='×';
        x.style.cssText='position:absolute;right:8px;top:6px;border:0;background:transparent;color:#fde68a;font-size:22px;cursor:pointer';
        x.onclick=function(){banner.remove();}; banner.appendChild(x); document.body.appendChild(banner);
      }
      const msg=$id('globalUpdateText'); if(msg) msg.textContent=text;
    }else{
      if(el){el.style.display='none';el.textContent='';}
      if(banner) banner.remove();
    }
  }catch(e){ if(el) el.style.display='none'; }
}
window.sysLoad = sysLoad; window.sysUse = sysUse; window.sysAction = sysAction; window.checkUpdates = checkUpdates;
window.wifiStatusShow = wifiStatusShow; window.wifiScan = wifiScan; window.wifiSaved = wifiSaved;
window.wifiConnect = wifiConnect; window.wifiDisconnect = wifiDisconnect;
window.wifiRadioToggle = wifiRadioToggle;
window.wifiForget = wifiForget; window.wifiReconnect = wifiReconnect;
window.wifiAutoconnect = wifiAutoconnect; window.wifiPriority = wifiPriority;
window.netParamsLoad = netParamsLoad; window.netApply = netApply;
setTimeout(checkUpdates, 2500); setInterval(checkUpdates, 6*60*60*1000);

/* ==================== 网络参数（IP / 掩码 / 网关 / DNS）==================== */
async function netParamsLoad(){
  const ifc = ($id('netIface') || {}).value || 'eth0';
  const msg = $id('netMsg');
  if(msg) msg.textContent = '读取中…';
  const d = await _sysAct('net-info', [ifc]);
  const t = d.message || '';
  const g = function(k){ const m = new RegExp('^' + k + '=(.*)$', 'm').exec(t); return m ? m[1].trim() : ''; };
  const set = function(id, v){ const e = $id(id); if(e) e.value = v || ''; };
  set('netIp', g('IP'));
  set('netMask', g('MASK') || g('PREFIX'));
  set('netGw', g('GW'));
  set('netDns', (g('DNS') || '').replace(/ /g, ','));
  const m = $id('netMode');
  if(m) m.value = (g('METHOD') === 'auto') ? 'dhcp' : 'static';
  if(msg) msg.textContent = g('CURIP') ? ('当前生效：' + g('CURIP')) : (g('CONN') ? '' : '该网卡暂无配置');
}
async function netApply(){
  const ifc = ($id('netIface') || {}).value || 'eth0';
  const mode = ($id('netMode') || {}).value || 'static';
  const ip = (($id('netIp') || {}).value || '').trim();
  const mask = (($id('netMask') || {}).value || '').trim();
  const gw = (($id('netGw') || {}).value || '').trim();
  const dns = (($id('netDns') || {}).value || '').trim().replace(/\s+/g, '');
  if(mode === 'static' && !ip){ toast('请先填写 IP 地址', 'error'); return; }
  const tip = (mode === 'dhcp')
    ? ('把 ' + ifc + ' 改为「自动获取 (DHCP)」？')
    : ('把 ' + ifc + ' 设为静态地址 ' + ip + ' / ' + (mask || '?') + '，网关 ' + gw + (dns ? ('，DNS ' + dns) : '') + ' ？'
       + '\n\n⚠ 若新地址与你正在使用的电脑不在同一网段，本页面会立即断开，需要用新地址重新访问。');
  if(!confirm(tip)) return;
  const msg = $id('netMsg');
  if(msg) msg.textContent = '应用中…（约 5 秒）';
  const d = await _sysAct('net-set', [ifc, ip, mask, gw, dns, mode]);
  if(msg) msg.textContent = d.ok ? '已应用' : '应用失败';
  alert(d.message || (d.ok ? '已应用' : '失败'));
  setTimeout(netParamsLoad, 2500);
}

window.rfPowerChanged = rfPowerChanged; window.rfPowerSync = rfPowerSync;
window._wifiFill = _wifiFill; window.sysWifiScan = wifiScan; window.sysWifiListConns = wifiSaved;
window.sysWifiConnect = wifiConnect; window.sysWifiDisconnect = wifiDisconnect;

/* ==================== 时间与同步 / 定时重启 / 维护诊断 ==================== */
function _sysOut(t, show){
  const o = $id('sysOut');
  if(!o) return;
  o.textContent = t || '';
  o.style.display = (show === false) ? 'none' : 'block';
  o.scrollTop = 0;
}
function sysOutClear(){ _sysOut('', false); }
async function _sysAct(action, args){
  try{
    const d = await (await fetch('/api/sys/action', {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({action: action, args: args || []})})).json();
    return d || {};
  }catch(e){ return {ok:false, message: '请求失败：' + (e && e.message ? e.message : e)}; }
}
async function sysTimeLoad(){
  const box = $id('sysTime'), sb = $id('sysSched');
  const d = await _sysAct('time-status', []);
  const t = d.message || '';
  const g = function(k){
    const m = new RegExp('^' + k + '=(.*)$', 'm').exec(t);
    return m ? m[1].trim() : '';
  };
  const ntp = g('NTP'), sy = g('SYNCED'), tz = g('TZ'), now = g('NOW');
  if(box){
    box.innerHTML = '当前时间：<b>' + _sysEsc(now || '-') + '</b>　时区：<b>' + _sysEsc(tz || '-') + '</b><br>'
      + 'NTP 自动校时：' + (ntp === 'yes' ? '<b style="color:#34d399">已开启</b>' : '<b style="color:#fbbf24">已关闭</b>')
      + '　同步状态：' + (sy === 'yes' ? '<b style="color:#34d399">已同步</b>' : '<span style="color:#fbbf24">未同步</span>');
  }
  const ti = $id('sysTzInput');
  if(ti && !ti.value && tz) ti.value = tz;
  if(sb){
    const on = (g('SCHED') === 'on');
    sb.innerHTML = on
      ? ('<b style="color:#34d399">已开启</b>　计划：' + _sysEsc(g('SCHED_CAL') || '-')
         + (g('SCHED_NEXT') ? ('　下次：' + _sysEsc(g('SCHED_NEXT'))) : ''))
      : '<span style="color:var(--dim)">未开启（保存后会写入 systemd timer）</span>';
  }
}
async function sysNtp(on){
  const d = await _sysAct('ntp', [on ? 'on' : 'off']);
  toast(d.ok ? ('NTP 已' + (on ? '开启' : '关闭')) : ('操作失败：' + (d.message || '')), d.ok ? 'ok' : 'error');
  sysTimeLoad();
}
async function sysSetTz(){
  const z = (($id('sysTzInput') || {}).value || '').trim();
  if(!z){ toast('请填写时区，例如 Asia/Shanghai', 'error'); return; }
  const d = await _sysAct('tz', [z]);
  toast(d.ok ? ('时区已设为 ' + z) : ('设置失败：' + (d.message || '')), d.ok ? 'ok' : 'error');
  _sysOut(d.message); sysTimeLoad();
}
async function sysTimeSync(){
  toast('正在同步时间…');
  const d = await _sysAct('timesync', []);
  _sysOut(d.message);
  toast(d.ok ? '已请求重新同步' : '同步失败', d.ok ? 'ok' : 'error');
  sysTimeLoad();
}
async function sysSchedSave(){
  const hh = (($id('sysSchedTime') || {}).value || '').trim();
  const md = (($id('sysSchedMode') || {}).value || 'daily');
  if(!/^([01]?\d|2[0-3]):[0-5]\d$/.test(hh)){ toast('时刻格式应为 HH:MM（24 小时制），如 04:00', 'error'); return; }
  const d = await _sysAct('sched-reboot', ['on', hh, md]);
  toast(d.ok ? ('已开启定时重启：' + (md === 'daily' ? '每天 ' : '每周一 ') + hh)
             : ('设置失败：' + (d.message || '')), d.ok ? 'ok' : 'error');
  _sysOut(d.message); sysTimeLoad();
}
async function sysSchedOff(){
  if(!confirm('关闭定时重启？')) return;
  const d = await _sysAct('sched-reboot', ['off']);
  toast(d.ok ? '定时重启已关闭' : ('关闭失败：' + (d.message || '')), d.ok ? 'ok' : 'error');
  _sysOut(d.message); sysTimeLoad();
}
async function sysBackup(){
  _sysOut('正在备份配置（打包 /home/pi 下配置与服务文件）…');
  const d = await _sysAct('backup', []);
  _sysOut(d.message || '(无输出)');
  toast(d.ok ? '备份完成（见输出里的文件路径）' : '备份失败', d.ok ? 'ok' : 'error');
}
async function sysLogs(){
  const svc = (($id('sysLogSvc') || {}).value || 'mmdvm-multi');
  const cnt = String((($id('sysLogN') || {}).value || '60')).replace(/[^0-9]/g, '') || '60';
  _sysOut('正在读取 ' + svc + ' 日志…');
  const d = await _sysAct('logs', [svc, cnt]);
  _sysOut('====== ' + svc + ' 最近 ' + cnt + ' 行 ======\n' + (d.message || '(无输出)'));
}
async function sysPing(){
  const h = (($id('sysPingHost') || {}).value || '').trim() || '223.5.5.5';
  _sysOut('正在 ping ' + h + ' …');
  const d = await _sysAct('ping', [h]);
  _sysOut('ping ' + h + '\n' + (d.message || '(无输出)'));
}
window.sysTimeLoad = sysTimeLoad; window.sysNtp = sysNtp; window.sysSetTz = sysSetTz;
window.sysTimeSync = sysTimeSync; window.sysSchedSave = sysSchedSave; window.sysSchedOff = sysSchedOff;
window.sysBackup = sysBackup; window.sysLogs = sysLogs; window.sysPing = sysPing;
window.sysOutClear = sysOutClear;

/* ==================== 通话记录（各通道最近 20 条）==================== */
function _flagCell(call){
  try{
    var c = (typeof lookupCallsignCountry === 'function') ? lookupCallsignCountry(call) : null;
    if(!c) return '<span style="color:var(--dim)">-</span>';
    return '<span title="' + _sysEsc(c.name) + '">' + c.flag + ' ' + _sysEsc(c.name) + '</span>';
  }catch(e){ return '<span style="color:var(--dim)">-</span>'; }
}
function _berCell(r){
  var b = String(r.ber == null ? '' : r.ber).trim();
  if(b && b !== '-'){
    var v = parseFloat(b), cls = 'good';
    if(v >= 5) cls = 'bad'; else if(v >= 2) cls = 'warn';
    return '<span class="ber ' + cls + '">' + _sysEsc(b) + '</span>';
  }
  var l = String(r.loss == null ? '' : r.loss).trim();
  if(l) return '<span style="color:var(--dim);font-size:11.5px" title="该模式日志没有 BER，只有丢包率">丢包 ' + _sysEsc(l) + '</span>';
  return '<span style="color:var(--dim)">-</span>';
}
function _durCell(r){
  var d = String(r.duration == null ? '' : r.duration).trim();
  if(d && d !== '通话中') return '<span style="font-family:Consolas,monospace">' + _sysEsc(d) + '</span>';
  var el = Number(r.elapsed || 0);
  if(el > 0) return '<span style="font-family:Consolas,monospace;color:#34d399" title="进行中，已用时长">▸ ' + el + 's</span>';
  return '<span style="color:var(--dim)">-</span>';
}
function _callRow(r, fresh){
  const srcColor = (r.src === 'RF') ? '#34d399' : '#60a5fa';
  const mm = /(S[12])$/.exec(String(r.mode || ''));
  const slot = mm ? ('<span class="qso-slot">' + mm[1] + '</span>') : '';
  const rssi = String(r.rssi || '').trim();
  return '<tr' + (fresh ? ' class="new"' : '') + '>'
    + '<td class="mono">' + _sysEsc(r.time || '--:--:--') + '</td>'
    + '<td><b>' + _sysEsc(r.call || '-') + '</b>' + slot + '</td>'
    + '<td>' + _flagCell(r.call) + '</td>'
    + '<td style="color:var(--muted)">' + _sysEsc(r.target || '-') + '</td>'
    + '<td>' + _durCell(r) + '</td>'
    + '<td>' + _berCell(r) + '</td>'
    + '<td style="color:' + srcColor + ';font-weight:800"' + (rssi ? (' title="RSSI ' + _sysEsc(rssi) + '"') : '') + '>'
    + _sysEsc(r.src || '-') + '</td>'
    + '</tr>';
}
var _callsSeen = {};      // 各通道上次看到的最新时间，用于标 NEW
var _callsTimer = null;   // 自动刷新定时器
var _callsAuto = true;
async function callsLoad(quiet){
  const box = $id('callsBox'), msg = $id('callsMsg');
  if(msg && !quiet) msg.textContent = '读取中…';
  try{
    const [d, q] = await Promise.all([
      fetch('/api/mc/channels?light=1', {credentials:'same-origin'}).then(function(r){ return r.json(); }),
      fetch('/api/qso', {credentials:'same-origin'}).then(function(r){ return r.json(); })
    ]);
    const chs = d.channels || [];
    const byCh = {};
    ((q && q.qso) || []).forEach(function(item){
      var m = /^CH(\d+)$/.exec(String(item.ch || ''));
      if(!m) return;
      var n = Number(m[1]);
      (byCh[n] = byCh[n] || []).push(item);
    });
    if(!box) return;
    if(!chs.length){
      box.innerHTML = '<div class="card"><div class="qso-empty">没有启用的通道</div></div>';
      return;
    }
    box.innerHTML = chs.map(function(c){
      const recs = (byCh[Number(c.n)] || []).slice(0, 30);
      const seen = _callsSeen[c.n] || '';
      _callsSeen[c.n] = recs.length ? String(recs[0].time || '') : '';
      const head = '通道 ' + c.n + (c.mode ? '　' + c.mode : '')
        + (c.rx ? '　' + (Number(c.rx) / 1e6).toFixed(4) + ' MHz' : '');
      const rows = recs.length
        ? ('<table class="qso-table"><thead><tr>'
           + '<th>时间</th><th>呼号</th><th>国家/地区</th><th>目标</th>'
           + '<th>时长</th><th>误码率</th><th>来源</th></tr></thead><tbody>'
           + recs.map(function(r){ return _callRow(r, !!seen && String(r.time || '') > seen); }).join('')
           + '</tbody></table>')
        : '<div class="qso-empty">暂无通联记录</div>';
      return '<div class="card" style="padding-bottom:0">'
        + '<div class="card-head" style="margin-bottom:10px"><div class="card-head-left"><div>'
        + '<div class="card-title">' + head + '</div>'
        + '<div class="card-sub">最近 ' + recs.length + ' 条（新 → 旧 · 时间=本机时区 · 误码率仅射频接收时有效）</div>'
        + '</div></div></div><div class="qso-wrap">' + rows + '</div></div>';
    }).join('');
    if(msg) msg.textContent = '更新于 ' + new Date().toLocaleTimeString();
    try{
      if(window.__repErr) window.__repErr('calls', 'channels=' + chs.length
        + ' boxlen=' + ((box && box.innerHTML) ? box.innerHTML.length : 0)
        + ' rows=' + ((box && box.querySelectorAll) ? box.querySelectorAll('div').length : -1),
        location.href, '', '');
    }catch(e2){}
  }catch(e){
    if(box) box.innerHTML = '<div class="card"><div class="qso-empty">读取失败：' + _sysEsc(e.message || e)
      + '<br><br>请按 Ctrl+F5 强制刷新；若仍失败，检查 mmdvm-web 服务状态。</div></div>';
    if(msg) msg.textContent = '';
  }
}
window.callsLoad = callsLoad;
function callsApplyAuto(){
  if(_callsTimer){ clearInterval(_callsTimer); _callsTimer = null; }
  const b = $id('callsAutoBtn');
  if(b) b.textContent = _callsAuto ? '⏱ 自动刷新：开' : '⏱ 自动刷新：关';
  if(!_callsAuto) return;
  _callsTimer = setInterval(function(){
    const p = $id('page-calls');
    if(p && p.style.display !== 'none') callsLoad(true);
  }, 10000);
}
function callsStopAuto(){ if(_callsTimer){ clearInterval(_callsTimer); _callsTimer = null; } }
function callsToggleAuto(){
  _callsAuto = !_callsAuto;
  callsApplyAuto();
  if(_callsAuto) callsLoad(true);
  toast(_callsAuto ? '已开启自动刷新（每 10 秒）' : '已关闭自动刷新', 'ok');
}
window.callsApplyAuto = callsApplyAuto; window.callsStopAuto = callsStopAuto;
window.callsToggleAuto = callsToggleAuto;

/* 模块 / 开机 联动到表单隐藏域 */
(function () {
  var mod = document.getElementById('spDstarMod');
  if (mod) {
    mod.addEventListener('change', function () {
      var hm = document.getElementById('dstar_module'); if (hm) hm.value = mod.value;
      var hr = document.getElementById('dstar_reflector_full');
      var cr = document.getElementById('dstar_reflector');
      if (hr && cr && cr.value) hr.value = cr.value + ' ' + mod.value;
    });
  }
  var st = document.getElementById('spDstarStartup');
  if (st) {
    st.addEventListener('change', function () {
      var hs = document.getElementById('dstar_atstartup'); if (hs) hs.value = st.value;
      document.querySelectorAll('input[name="dstar_startup"]').forEach(function (r) {
        r.checked = (r.value === st.value);
      });
      var hr = document.getElementById('dstar_reflector_full');
      var cr = document.getElementById('dstar_reflector');
      var md = document.getElementById('spDstarMod');
      if (hr && cr && cr.value && md) hr.value = cr.value + ' ' + md.value;
    });
  }
})();

</script>



<!-- ===== 射频模式配置 页面脚本（独立块，隔离） ===== -->
<script>

/* ==================== 频率输入习惯（与主控制台同一套） ====================
   输入 439.500 -> 439500000 Hz ；输入 439.5125 -> 439512500 Hz
   显示 439.500 / 439.5125。主控制台已有 window.FREQ_HZ / FREQ_MHZ，这里做兜底。 */
var RFM_HZ = (typeof window !== 'undefined' && window.FREQ_HZ) ? window.FREQ_HZ : function(v){
  if(v===null||v===undefined) return 0;
  var s=String(v).trim().replace(/[^0-9.]/g,'');
  if(s==='') return 0;
  var f=parseFloat(s); if(!isFinite(f)||f<=0) return 0;
  var mhz=(s.indexOf('.')>=0||f<100000);
  return Math.round(mhz? f*1000000 : f);
};
var RFM_MHZ = (typeof window !== 'undefined' && window.FREQ_MHZ) ? window.FREQ_MHZ : function(hz){
  hz=parseInt(hz,10); if(!isFinite(hz)||hz<=0) return '';
  var d=(hz%12500===0 && hz%25000!==0)?4:3;
  return (hz/1000000).toFixed(d);
};

var RFM_MODES = ['DMR','D-STAR','YSF','P25','NXDN','FM','POCSAG','NONE'];
var RFM_COL = {'DMR':'#0ea5e9','D-STAR':'#8b5cf6','YSF':'#f97316','NXDN':'#2563eb',
               'P25':'#e11d48','FM':'#10b981','POCSAG':'#f59e0b','NONE':'#64748b'};
var RFM_MAX = 6;          /* ★ 只用 6 路通道（第 7 路用不上，已去掉） */
var RFM_PLAN = null;      /* 最近一次 /api/mc/plan 的结果 */
var RFM_CHS = [];         /* 最近一次 /api/mc/channels 的通道表 */
var RFM_HW = {};          /* 当前设备硬件型号与通道上限 */
var RFM_ID = '';          /* 台站 DMR Id（保存 RF 时要回传，避免被清空） */
var RFM_RX = '', RFM_TX = '';   /* MMDVM-Host 里的频率原值（回传，不改动） */

function rfmQ(id){ return document.getElementById(id); }
function rfmSvg(m){
  var M=String(m||'').toUpperCase(), col=RFM_COL[M]||'#64748b', g='';
  if(M==='DMR') g='<rect x="9" y="15" width="22" height="4" rx="2" fill="#fff"/><rect x="9" y="23" width="14" height="4" rx="2" fill="#fff" opacity=".75"/>';
  else if(M==='D-STAR') g='<path d="M20 10l2.6 6.1 6.4.6-4.9 4.3 1.5 6.4L20 24l-5.6 3.4 1.5-6.4-4.9-4.3 6.4-.6z" fill="#fff"/>';
  else if(M==='YSF') g='<path d="M20 10l8 10-8 10-8-10z" fill="#fff"/>';
  else if(M==='NXDN') g='<path d="M13 27V13h4.2l5.6 8V13H27v14h-4.2l-5.6-8v8z" fill="#fff"/>';
  else if(M==='P25') g='<path d="M20 10l9 3.4v7.1c0 5-3.7 8.2-9 9.5-5.3-1.3-9-4.5-9-9.5v-7.1z" fill="#fff"/>';
  else if(M==='FM') g='<path d="M9 20c2.6-6 5.2-6 7.8 0s5.2 6 7.8 0 5.2-6 5.4 0" stroke="#fff" stroke-width="3" fill="none" stroke-linecap="round"/>';
  else g='<circle cx="20" cy="20" r="6" fill="#fff" opacity=".7"/>';
  return '<svg viewBox="0 0 40 40" width="28" height="28" xmlns="http://www.w3.org/2000/svg" style="flex:0 0 28px"><rect x="2" y="2" width="36" height="36" rx="9" fill="'+col+'"/>'+g+'</svg>';
}
function rfmPost(url, body, cb){
  fetch(url, {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})})
    .then(function(r){ return r.json(); })
    .then(function(d){ cb(d); })
    .catch(function(e){ if(typeof toast==='function') toast('失败：'+e,false); });
}
function rfmMsg(id, txt, ok){
  var e=rfmQ(id); if(e) e.textContent=txt||'';
  if(txt && typeof toast==='function') toast(txt, ok);
}
/* ==================== 硬件网格（与后端 channel-setup.py 一致） ====================
   ch1=中心, ch2=+25k, ch3=-25k, ch4=-50k, ch5=+75k …（SampleRate/25000 = PFB bin 数） */
function rfmGridStep(i, sr){
  sr = parseInt(sr,10) || parseInt((RFM_PLAN&&RFM_PLAN.sample_rate)||0,10) || 300000;
  var n = Math.max(1, Math.round(sr/25000));
  var mf = Math.max(1, Math.min(Math.floor(n/2)-1, 4));
  var p = i-1, b = (p<mf) ? p : (n-1)-(p-mf);
  return (b*2<=n) ? b : b-n;
}
function rfmSpacingHz(){
  var v = parseFloat((rfmQ('rfmGap')||{}).value);
  return isFinite(v)&&v>0 ? Math.round(v*1000) : 25000;
}
function rfmCenterRx(){ return RFM_HZ((rfmQ('rfmCrx')||{}).value); }
function rfmCenterTx(){ return RFM_HZ((rfmQ('rfmCtx')||{}).value); }
function rfmChanRx(i){ return rfmCenterRx() + rfmGridStep(i)*rfmSpacingHz(); }
function rfmChanTx(i){ return rfmCenterTx() + rfmGridStep(i)*rfmSpacingHz(); }
function rfmNChSel(){
  var v = parseInt((rfmQ('rfmNCh')||{}).value,10);
  return (isFinite(v)&&v>=1) ? Math.min(RFM_MAX, v) : 6;
}

/* ==================== 台站参数（呼号 / 功率 / 增益） ==================== */
function rfmFlat(d){
  var flat={};
  Object.keys(d||{}).forEach(function(k){
    var v=d[k];
    if(v && typeof v==='object'){ Object.keys(v).forEach(function(k2){ flat[k2]=v[k2]; flat[String(k2).toLowerCase()]=v[k2]; }); }
    else { flat[k]=v; }
  });
  return flat;
}
function rfmPick(flat){
  for(var i=1;i<arguments.length;i++){
    var k=arguments[i], lk=String(k).toLowerCase();
    if(flat[k]!==undefined && flat[k]!==null && String(flat[k])!=='') return flat[k];
    if(flat[lk]!==undefined && flat[lk]!==null && String(flat[lk])!=='') return flat[lk];
  }
  return '';
}
function rfmLoadStation(){
  fetch('/api/config/RF', {credentials:'same-origin'}).then(function(r){ return r.json(); })
  .then(function(d){
    var f=rfmFlat(d);
    var c=rfmPick(f,'Callsign','callsign');
    if(c && c!=='NOCALL' && rfmQ('rfmCall')) rfmQ('rfmCall').value=String(c).toUpperCase();
    var p=rfmPick(f,'Power','power');
    if(p!=='' && rfmQ('rfmPower')) rfmQ('rfmPower').value=(String(p).trim()==='0'?'0':'7');
    var rx=rfmPick(f,'RxGain','rxgain');
    if(rx!=='' && rfmQ('rfmRxGain')) rfmQ('rfmRxGain').value=String(rx);
    var tx=rfmPick(f,'TxGain','txgain');
    if(tx!=='' && rfmQ('rfmTxGain')) rfmQ('rfmTxGain').value=String(tx);
    /* 缓存台站 Id 与 MMDVM-Host 频率原值：保存时原样回传，不动无关配置 */
    RFM_ID = String(rfmPick(f,'Id','id')||'');
    RFM_RX = String(rfmPick(f,'RXFrequency','rxfreq')||'');
    RFM_TX = String(rfmPick(f,'TXFrequency','txfreq')||'');
  }).catch(function(){});
}
function rfmPowerChanged(){
  var sel=rfmQ('rfmPower'), tg=rfmQ('rfmTxGain');
  if(!sel||!tg) return;
  tg.value = (String(sel.value).trim()==='0') ? 10 : 30;
}
function rfmSaveStation(){
  var call=String((rfmQ('rfmCall')||{}).value||'').trim().toUpperCase();
  var p=(rfmQ('rfmPower')||{}).value, rx=(rfmQ('rfmRxGain')||{}).value, tx=(rfmQ('rfmTxGain')||{}).value;
  if(!call){ rfmMsg('rfmStMsg','请先填写本机呼号', false); return; }
  if(call==='NOCALL'){ rfmMsg('rfmStMsg','呼号不能是 NOCALL', false); return; }
  rfmMsg('rfmStMsg','正在保存…');
  /* ★ 接口是 /api/save/RF（大写！），字段名与页面表单一致：
     callsign / id / rxfreq / txfreq / duplex / power / rxgain / txgain
     —— id 与频率用读回来的原值回传，不改动无关配置 */
  rfmPost('/api/save/RF',{callsign:call,
                          id: RFM_ID || String((RFM_PLAN&&RFM_PLAN.dmr_id)||''),
                          rxfreq: RFM_RX, txfreq: RFM_TX, duplex:'1',
                          power:String(p), rxgain:String(rx), txgain:String(tx)}, function(d){
    var ok=!!(d&&d.ok!==false);
    var t=String((d&&(d.message||d.out||d.error))||'');
    if(!ok){ rfmMsg('rfmStMsg','❌ '+t.slice(0,80), false); return; }
    var body=rfmPlanPayload(call);      /* 呼号同时同步到各网关（仅保存，不重启） */
    if(!body){ rfmMsg('rfmStMsg','✅ 呼号/功率/增益已保存'); return; }
    rfmPost('/api/mc/plan-save', body, function(r2){
      var ok2=!!(r2&&r2.ok!==false);
      rfmMsg('rfmStMsg', ok2? '✅ 已保存，并同步到各网关'
                            : '⚠ 已保存，但网关呼号同步失败：'+String((r2&&(r2.out||r2.error))||'').slice(0,60), ok2);
      if(RFM_PLAN) RFM_PLAN.callsign=call;
    });
  });
}
/* sampling: 采样率随通道数自动切 —— 1-5 路 150k / 6-11 路 300k（硬件只有这两档）*/
/* ==================== 信道规划：读取 / 渲染 ==================== */
function rfmFillNCh(n){
  var sel=rfmQ('rfmNCh'); if(!sel) return;
  var h='';
  for(var i=1;i<=RFM_MAX;i++){
    h+='<option value="'+i+'"'+(i===n?' selected':'')+'>'+i+' 路通道'+(i===1?'（单通道）':'（同时收发）')+'</option>';
  }
  sel.innerHTML=h;
}
function rfmPlanLoad(){
  fetch('/api/mc/plan', {credentials:'same-origin'}).then(function(r){ return r.json(); })
  .then(function(d){
    if(!d || d.ok===false){
      var bx=rfmQ('rfmRows');
      if(bx) bx.innerHTML='<div class="sp-empty">读取规划失败：'+String((d&&(d.out||d.error))||'')+'</div>';
      return;
    }
    RFM_PLAN=d;
    var n=parseInt(d.channels||1,10)||1;
    rfmFillNCh(n);
    if(rfmQ('rfmCrx')) rfmQ('rfmCrx').value=RFM_MHZ(d.center_rx)||RFM_MHZ(434450000);
    if(rfmQ('rfmCtx')) rfmQ('rfmCtx').value=RFM_MHZ(d.center_tx)||RFM_MHZ(439450000);
    if(rfmQ('rfmGap')){
      var sp=parseInt(d.spacing||25000,10);
      rfmQ('rfmGap').value=(sp<20000)?'12.5':'25';
    }
    if(rfmQ('rfmCall') && d.callsign && !rfmQ('rfmCall').value) rfmQ('rfmCall').value=String(d.callsign).toUpperCase();
    rfmRenderRows();
    var m=rfmQ('rfmPlanMsg'); if(m) m.textContent='';
  }).catch(function(e){ if(typeof toast==='function') toast('读取规划失败：'+e,false); });
}
function rfmRenderRows(){
  var box=rfmQ('rfmRows'); if(!box) return;
  var n=rfmNChSel();
  var plan={}; ((RFM_PLAN&&RFM_PLAN.list)||[]).forEach(function(c){ plan[parseInt(c.n,10)]=c; });
  var h='<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px">';
  for(var i=1;i<=RFM_MAX;i++){
    var c=plan[i]||{n:i,mode:'NONE'};
    var on=(i<=n);
    var opts=RFM_MODES.map(function(m){
      return '<option value="'+m+'"'+(String(c.mode||'NONE').toUpperCase()===m?' selected':'')+'>'+m+'</option>';
    }).join('');
    h+='<div class="fld" data-n="'+i+'" style="border:1px solid var(--border);border-radius:10px;padding:11px;background:var(--card2);'+(on?'':'opacity:.45')+'">'
      +   '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
      +     '<span style="width:8px;height:8px;border-radius:9px;flex:0 0 8px;background:'+(on?'var(--green2)':'#64748b')+'"></span>'
      +     '<b style="font-size:13.5px;color:#7dd3fc">通道 '+i+'</b>'
      +     (on?''+'<span class="hint">已启用</span>':'<span class="hint">未启用</span>')
      +     '<span style="margin-left:auto;display:flex;align-items:center;gap:7px;color:#fff;font-weight:800;font-size:13.5px">'
      +     rfmSvg(c.mode)+String(c.mode||'NONE')+'</span>'
      +   '</div>'
      +   '<div style="display:flex;gap:7px;align-items:center;margin-bottom:7px">'
      +     '<span class="hint" style="flex:0 0 26px">RX</span>'
      +     '<input class="rfm-rx" type="text" inputmode="decimal" value="'+RFM_MHZ(((c.rx&&c.rx>0)?c.rx:rfmChanRx(i)))+'" style="flex:1;min-width:0" onchange="rfmRowEdited('+i+')">'
      +     '<span class="rfm-fq hint" style="flex:0 0 96px;text-align:right">'+RFM_MHZ(rfmChanRx(i))+'</span>'
      +   '</div>'
      +   '<div style="display:flex;gap:7px;align-items:center;margin-bottom:9px">'
      +     '<span class="hint" style="flex:0 0 26px">TX</span>'
      +     '<input class="rfm-tx" type="text" inputmode="decimal" value="'+RFM_MHZ(((c.tx&&c.tx>0)?c.tx:rfmChanTx(i)))+'" style="flex:1;min-width:0" onchange="rfmRowEdited('+i+')">'
      +     '<span class="rfm-fq hint" style="flex:0 0 96px;text-align:right">'+RFM_MHZ(rfmChanTx(i))+'</span>'
      +   '</div>'
      +   '<div style="display:flex;gap:6px;align-items:center">'
      +     '<select class="rfm-mode" style="flex:1;min-width:0"'+(on?'':' disabled')+'>'+opts+'</select>'
      +     '<button class="btn btn-ghost btn-sm" onclick="rfmRowGrid('+i+')" title="按中心频率 + 硬件网格重算">按间距算</button>'
      +   '</div>'
      + '</div>';
  }
  h+='</div>';
  box.innerHTML=h;
  rfmPlanFreqs(false);
}
function rfmPlanFreqs(force){
  var box=rfmQ('rfmRows'); if(!box) return;
  var n=rfmNChSel();
  var cards=box.querySelectorAll('.fld[data-n]');
  Array.prototype.forEach.call(cards,function(cd){
    var i=parseInt(cd.getAttribute('data-n'),10), on=(i<=n);
    cd.style.opacity = on? '' : '.45';
    var r=cd.querySelector('.rfm-rx'), t=cd.querySelector('.rfm-tx');
    var md=cd.querySelector('.rfm-mode'); if(md) md.disabled=!on;
    var fq=cd.querySelectorAll('.rfm-fq');
    if(force){
      if(r) r.value=RFM_MHZ(rfmChanRx(i));
      if(t) t.value=RFM_MHZ(rfmChanTx(i));
    }
    var rv=r?RFM_HZ(r.value):0, tv=t?RFM_HZ(t.value):0;
    if(rv<=0 && r){ r.value=RFM_MHZ(rfmChanRx(i)); rv=RFM_HZ(r.value); }
    if(tv<=0 && t){ t.value=RFM_MHZ(rfmChanTx(i)); tv=RFM_HZ(t.value); }
    if(fq.length>=2){ fq[0].textContent=rv>0?RFM_MHZ(rv):'--'; fq[1].textContent=tv>0?RFM_MHZ(tv):'--'; }
  });
  if(rfmQ('rfmCrxHz')) rfmQ('rfmCrxHz').textContent='= '+rfmCenterRx()+' Hz';
  if(rfmQ('rfmCtxHz')) rfmQ('rfmCtxHz').textContent='= '+rfmCenterTx()+' Hz';
  rfmBanner();
}
function rfmCenterTyped(){ rfmPlanFreqs(true); }
function rfmNChChanged(){ rfmRenderRows(); }
function rfmRowGrid(i){
  var cd=document.querySelector('#rfmRows .fld[data-n="'+i+'"]'); if(!cd) return;
  var r=cd.querySelector('.rfm-rx'), t=cd.querySelector('.rfm-tx');
  if(r) r.value=RFM_MHZ(rfmChanRx(i));
  if(t) t.value=RFM_MHZ(rfmChanTx(i));
  rfmPlanFreqs(false);
}
/* 用户改某一路：保留绝对频率，后端自动选择中心频率和 PFB bin。 */
function rfmRowEdited(i){ rfmPlanFreqs(false); }
/* ==================== 提交规划（必须带 center/spacing/sample_rate，后端按它们算每路频率） ==================== */
function rfmPlanPayload(callOverride){
  var cx=rfmCenterRx(), ct=rfmCenterTx();
  if(!cx||!ct) return null;
  var n=rfmNChSel(), sp=rfmSpacingHz();
  var old={}; ((RFM_PLAN&&RFM_PLAN.list)||[]).forEach(function(c){ old[parseInt(c.n,10)]=c; });
  var list=[];
  for(var i=1;i<=RFM_MAX;i++){
    var sel=document.querySelector('#rfmRows .fld[data-n="'+i+'"] .rfm-mode');
    var mode = sel ? sel.value : String((old[i]&&old[i].mode)||'NONE').toUpperCase();
    /* ★ 超出通道数的通道：模式照原样提交（后端只启停服务，不动保存的模式）
       —— 这样「通道数量」调小再调大时，各通道原来的协议还在，可以完整恢复多通道 */
    var row=document.querySelector('#rfmRows .fld[data-n="'+i+'"]');
    var rEl=row?row.querySelector('.rfm-rx'):null, tEl=row?row.querySelector('.rfm-tx'):null;
    var rv=rEl?RFM_HZ(rEl.value):0, tv=tEl?RFM_HZ(tEl.value):0;
    if(!rv) rv=cx+rfmGridStep(i)*sp;
    if(!tv) tv=ct+rfmGridStep(i)*sp;
    list.push({n:i, mode:mode, rx:rv, tx:tv});
  }
  var call = (callOverride!==undefined) ? callOverride
             : String((rfmQ('rfmCall')||{}).value||'').trim().toUpperCase();
  return {channels:n, center_rx:cx, center_tx:ct, spacing:sp,
          sample_rate:(n<=5?150000:300000),   /* ★ 自适应：≤5 路 150k / ≥6 路 300k */
          callsign: call || String((RFM_PLAN&&RFM_PLAN.callsign)||''),
          dmr_id: String((RFM_PLAN&&RFM_PLAN.dmr_id)||''),
          list:list};
}
function rfmApplyConfirm(body, onlySave){
  if(onlySave) return true;
  var legal = '请遵守所在国家或地区关于无线电发射设备、频率使用和操作资格的法律法规。因违规使用造成的后果由操作者承担，与本软件及作者无关。';
  var hw=RFM_HW||{};
  var model=String(hw.model||'').toLowerCase();
  var n=parseInt((body&&body.channels)||0,10)||0;
  var max=parseInt(hw.max_channels||0,10)||4;
  if(model.indexOf('raspberry pi 3')>=0){
    if(n>max){
      alert('检测到当前设备为树莓派 3B。\n\n本机最多支持 '+max+' 路射频，请先减少到 '+max+' 路以内再保存。');
      return false;
    }
    return confirm('检测到当前设备为树莓派 3B。\n\n为了保证语音稳定和系统余量，建议同时启用 2 路射频；确实需要时可以开启最多 4 路，不建议超过 4 路。\n\n当前计划：'+n+' 路射频。\n\n'+legal+'\n\n点击“确定”后将保存并应用该配置。');
  }
  return confirm('将写入 MMDVM-Multi.ini 与各通道配置，并重启受影响的通道。\n大约需要 20-60 秒。\n\n'+legal+'\n\n继续？');
}
function rfmPlanApply(onlySave){
  var body=rfmPlanPayload();
  if(!body){ rfmMsg('rfmPlanMsg','请先填写中心频率（MHz）', false); return; }
  if(!rfmApplyConfirm(body, onlySave)) return;
  rfmMsg('rfmPlanMsg', onlySave?'正在保存规划…':'正在应用规划（约 20-60 秒）…');
  rfmPost('/api/mc/plan-'+(onlySave?'save':'apply'), body, function(d){
    var ok=!!(d&&d.ok!==false);
    var t=String((d&&(d.out||d.message||d.error))||'').trim();
    var legal = '{legal}';
    rfmMsg('rfmPlanMsg',(ok?'✅ ':'❌ ')+t.slice(0,120)+(ok?' ｜ '+legal:''), ok);
    if(ok) setTimeout(function(){ rfmPlanLoad(); rfmLoad(); rfmModeStatus(); }, 2500);
  });
}
/* ==================== 各通道状态（永远列出 1-6，含未启用） ==================== */
function rfmBanner(){
  var b=rfmQ('rfmBanner'); if(!b) return;
  var n=rfmNChSel(), arr=[];
  for(var i=1;i<=n;i++){
    var cd=document.querySelector('#rfmRows .fld[data-n="'+i+'"]');
    var r=cd?cd.querySelector('.rfm-rx'):null, t=cd?cd.querySelector('.rfm-tx'):null;
    var rx=(r&&RFM_HZ(r.value)>0)?RFM_HZ(r.value):rfmChanRx(i);
    var tx=(t&&RFM_HZ(t.value)>0)?RFM_HZ(t.value):rfmChanTx(i);
    arr.push('CH'+i+' '+RFM_MHZ(rx)+'/'+RFM_MHZ(tx));
  }
  b.textContent = arr.join('  ｜  ') || '（未配置）';
}
function rfmLoad(){
  fetch('/api/mc/channels?light=1', {credentials:'same-origin'}).then(function(r){ return r.json(); })
  .then(function(d){
    if(!d || d.ok===false) return;
    var live={};
    (d.channels||[]).forEach(function(c){ live[parseInt(c.n,10)]=c; });
    RFM_CHS=d.channels||[];
    RFM_HW=d.hardware||{};
    var planList={}; ((RFM_PLAN&&RFM_PLAN.list)||[]).forEach(function(c){ planList[parseInt(c.n,10)]=c; });
    if(typeof mcSeqBuild==='function') mcSeqBuild(d.channels||[]);
    var box=rfmQ('rfmChans'); if(!box) return;
    var h='<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px">';
    for(var i=1;i<=RFM_MAX;i++){
      var c=live[i]||null;
      var mode=c?String(c.mode||'NONE'):String((planList[i]&&planList[i].mode)||'NONE');
      var rx=(c&&c.rx)?c.rx:((planList[i]&&planList[i].rx)||0);
      var tx=(c&&c.tx)?c.tx:((planList[i]&&planList[i].tx)||0);
      var run=!!(c&&c.iq&&c.host), on=!!c;
      var opts=RFM_MODES.map(function(m){
        return '<option value="'+m+'"'+(mode.toUpperCase()===m?' selected':'')+'>'+m+'</option>';
      }).join('');
      h+='<div class="fld" data-ch="'+i+'" style="border:1px solid var(--border);border-radius:10px;padding:11px;background:var(--card2);'+(on?'':'opacity:.5')+'">'
        +   '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
        +     '<span style="width:8px;height:8px;border-radius:9px;flex:0 0 8px;background:'+(run?'var(--green2)':(on?'var(--amber2)':'#64748b'))+'"></span>'
        +     '<b style="font-size:13.5px;color:#7dd3fc">通道 '+i+'</b>'
        +     (on?'':'<span class="hint">未启用</span>')
        +     '<span style="margin-left:auto;display:flex;align-items:center;gap:7px;color:#fff;font-weight:800;font-size:13.5px">'
        +     rfmSvg(mode)+mode+'</span>'
        +   '</div>'
        +   '<div class="hint" style="margin-bottom:8px">RX '+RFM_MHZ(rx)+' / TX '+RFM_MHZ(tx)+' MHz'
        +     (on?((c.iq?' ｜ IQ ✓':' ｜ IQ ✗')+(c.host?' Host ✓':' Host ✗')):'')+'</div>'
        +   '<div style="display:flex;gap:6px;align-items:center">'
        +     '<select class="rfm-mode2" style="flex:1;min-width:0"'+(on?'':' disabled')+'>'+opts+'</select>'
        +     '<button class="btn btn-green btn-sm"'+(on?'':' disabled')+' onclick="rfmSwitch('+i+')">切换</button>'
        +     '<button class="btn btn-ghost btn-sm"'+(on?'':' disabled')+' onclick="rfmRestart('+i+')">重启</button>'
        +   '</div>'
        + '</div>';
    }
    h+='</div>';
    box.innerHTML=h;
    rfmBanner();
  }).catch(function(){});
}
function rfmSwitch(n){
  var card=document.querySelector('#rfmChans .fld[data-ch="'+n+'"]'); if(!card) return;
  var c2=card.querySelector('.rfm-mode2'); var mode=c2?c2.value:'NONE';
  if(!confirm('把射频通道 '+n+' 切换为 '+mode+' ？将重启该通道。')) return;
  if(typeof toast==='function') toast('通道 '+n+' 正在切换…');
  rfmPost('/api/mc/switch',{ch:n,mode:mode},function(d){
    var ok=!!(d&&d.ok);
    if(typeof toast==='function') toast(ok?('通道 '+n+' 已切换为 '+mode):('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok) setTimeout(function(){ rfmLoad(); rfmPlanLoad(); }, 1800);
  });
}
function rfmRestart(n){
  if(!confirm('重启射频通道 '+n+' 的基带与主机？')) return;
  rfmPost('/api/mc/restart',{ch:n},function(d){
    var ok=!!(d&&d.ok);
    if(typeof toast==='function') toast(ok?('通道 '+n+' 已重启'):('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok) setTimeout(rfmLoad,1800);
  });
}
function rfmRestartAll(){
  if(!confirm('重启 MMDVM-Multi 引擎与全部通道？约 20~40 秒。')) return;
  rfmPost('/api/mc/multi-restart',{},function(d){
    var ok=!!(d&&d.ok);
    if(typeof toast==='function') toast(ok?'引擎与全部通道已重启':('失败：'+String((d&&(d.error||d.out))||'')),ok);
    if(ok) setTimeout(function(){ rfmLoad(); rfmPlanLoad(); }, 5000);
  });
}
document.addEventListener('change', function(e){
  if(e.target && e.target.id==='rfmPower') rfmPowerChanged();
});
(function(){ var s=rfmQ('rfmPower'); if(s) rfmPowerChanged(); })();
/* 兜底：规划读取失败时页面也能用（默认中心频率 + 通道数选择器 + 通道行） */
(function(){
  if(!rfmQ('rfmNCh')) return;
  if(!rfmQ('rfmNCh').innerHTML) rfmFillNCh(6);
  if(rfmQ('rfmCrx') && !rfmQ('rfmCrx').value) rfmQ('rfmCrx').value='434.450';
  if(rfmQ('rfmCtx') && !rfmQ('rfmCtx').value) rfmQ('rfmCtx').value='439.450';
  if(rfmQ('rfmRows') && !document.querySelector('#rfmRows .fld[data-n]')) rfmRenderRows();
})();

/* 点侧栏进入本页时自动加载：规划 + 通道状态 + 台站参数 */
document.addEventListener("click", function(e){
  var el = e.target.closest && e.target.closest('[data-page="rfmode"]');
  if(!el) return;
  setTimeout(function(){
    if(typeof rfmPlanLoad==="function") rfmPlanLoad();
    if(typeof rfmLoad==="function") rfmLoad();
    if(typeof rfmLoadStation==="function") rfmLoadStation();
    if(typeof rfmModeStatus==="function") rfmModeStatus();
  }, 120);
});
</script>

<!-- ===== 引擎监控 页面脚本（独立块，隔离；给射频模式配置页的诊断卡用） ===== -->
<script>

var PLN = {channels:0, sample_rate:150000, callsign:'', list:[]};   /* 主控制台读它（规划摘要） */
var CH_GAP = 25000;
function chGridStep(i, sr){
  sr = parseInt(sr,10) || parseInt((typeof PLN==='object'&&PLN&&PLN.sample_rate)||0,10) || 300000;
  var n = Math.max(1, Math.round(sr/CH_GAP));
  var mf = Math.max(1, Math.min(Math.floor(n/2)-1, 4));
  var p = i-1, b = (p<mf) ? p : (n-1)-(p-mf);
  return (b*2<=n) ? b : b-n;
}
var MC_HZ = (typeof window!=='undefined' && window.FREQ_HZ) ? window.FREQ_HZ : function(v){
  var s=String(v==null?'':v).trim().replace(/[^0-9.]/g,''); var f=parseFloat(s);
  if(!isFinite(f)||f<=0) return 0;
  return Math.round((s.indexOf('.')>=0||f<100000)? f*1000000 : f);
};
var MC_MHZ = (typeof window!=='undefined' && window.FREQ_MHZ) ? window.FREQ_MHZ : function(hz){
  hz=parseInt(hz,10); if(!isFinite(hz)||hz<=0) return '';
  return (hz/1000000).toFixed((hz%12500===0&&hz%25000!==0)?4:3);
};
function mcI(id){ return document.getElementById(id); }
function mcEsc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function mcDot(on){ return '<span style="display:inline-block;width:7px;height:7px;border-radius:9px;margin-right:5px;background:'+(on?'var(--green2)':'#ef4444')+'"></span>'; }
function mcKV(k,v){ return '<div class="fld" style="background:var(--card2);border:1px solid var(--border);border-radius:9px;padding:9px 11px">'
  + '<div class="hint" style="margin-bottom:3px">'+mcEsc(k)+'</div>'
  + '<div style="font-size:14.5px;font-weight:700;color:#dfe8f5">'+mcEsc(v)+'</div></div>'; }
function mcMsg(t, ok){
  var e=mcI('mcMsg'); if(e) e.textContent=t||'';
  if(t && typeof toast==='function') toast(t, ok);
}
function mcPlanLite(){
  return fetch('/api/mc/plan', {credentials:'same-origin'}).then(function(r){ return r.json(); })
  .then(function(p){
    if(p && p.ok!==false && p.list){ PLN=p; if(!PLN.sample_rate) PLN.sample_rate=150000; }
    return PLN;
  }).catch(function(){ return PLN; });
}
function mcRefreshAll(){
  mcPlanLite();
  mcModeLite(function(){
    fetch('/api/mc/channels?light=1', {credentials:'same-origin'}).then(function(r){ return r.json(); })
    .then(function(d){
      if(!d || d.ok===false){ mcMsg('读取失败'+String((d&&(d.error||d.out))||''), false); return; }
      if(typeof mcSeqBuild==='function') mcSeqBuild(d.channels||[]);
      mcRenderEng(d); mcRenderChs(d); mcRenderSvcs(d);
    }).catch(function(e){ mcMsg('网络错误: '+e, false); });
  });
}
var MC_MODE_TXT = '--';
function mcModeLite(cb){ MC_MODE_TXT='正常模式'; if(cb) cb(); }

function mcRenderEng(d){
  var e=d.multi||{}, box=mcI('mcEngBox'); if(!box) return;
  var nch = parseInt((PLN&&PLN.channels)||0,10) || (d.channels||[]).length;
  box.innerHTML = mcKV('当前模式', MC_MODE_TXT)
    + mcKV('引擎状态', e.active?'运行中':'已停止')
    + mcKV('运行通道数', nch + ' / 配置 ' + (e.channels||'-'))
    + mcKV('采样率', e.rate? (e.rate+' Hz'):'--')
    + mcKV('SDR 类型', e.sdr||'--')
    + mcKV('RX 中心', e.rx? (MC_MHZ(e.rx)+' MHz'):'--')
    + mcKV('TX 中心', e.tx? (MC_MHZ(e.tx)+' MHz'):'--')
    + mcKV('RX 增益', (e.rxg===''||e.rxg==null)?'--':e.rxg+' dB')
    + mcKV('TX 增益', (e.txg===''||e.txg==null)?'--':e.txg+' dB');
  var b=mcI('mcChBadge'); if(b) b.textContent='IQ/Host '+(d.channels||[]).length+' 路';
}
function mcRenderChs(d){
  var cs=d.channels||[], box=mcI('mcChBox'); if(!box) return;
  var h='';
  for(var i=0;i<cs.length;i++){
    var c=cs[i];
    h+='<div class="fld" style="border:1px solid var(--border);border-radius:10px;padding:11px;background:var(--card2);margin-bottom:9px">'
      + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:7px">'
      +   '<b style="font-size:13.5px;color:#7dd3fc">射频通道 '+(typeof mcSeq==='function'?mcSeq(c.n):c.n)+'</b>'
      +   '<span class="hint">'+mcEsc(c.mode)+'</span>'
      +   '<span style="margin-left:auto" class="hint">'+mcDot(c.iq)+'IQ '+mcDot(c.host)+'Host</span>'
      + '</div>'
      + '<div class="form-grid" style="margin:0">'
      +   mcKV('RX 频率', c.rx? (MC_MHZ(c.rx)+' MHz') : '--')
      +   mcKV('TX 频率', c.tx? (MC_MHZ(c.tx)+' MHz') : '--')
      +   mcKV('Multi 端口', c.mport)+mcKV('Host 端口', c.hport)
      + '</div>'
      + '<div class="hint" style="margin-top:8px">最近通联：'+mcEsc(c.lastq||'（无）')+'</div>'
      + '<div class="mc-foot" style="margin:8px 0 0">'
      +   '<button class="btn btn-ghost btn-sm" onclick="mcRestartCh('+c.n+')">&#8634; 重启本通道</button>'
      + '</div></div>';
  }
  box.innerHTML = h || '<div class="sp-empty">未检测到通道配置</div>';
}
function mcRenderSvcs(d){
  var sv=d.services||[], box=mcI('mcSvcBox'); if(!box) return;
  var h='';
  for(var i=0;i<sv.length;i++){ h+=mcKV(sv[i].name, (sv[i].active?'● 运行':'○ 停止')); }
  box.innerHTML = h || '<div class="hint">无服务信息</div>';
  var b=mcI('mcSvcBadge');
  if(b){ var on=sv.filter(function(x){return x.active;}).length; b.textContent=on+' / '+sv.length+' 在运行'; }
  mcFillLogCh(d.channels||[]);
}
function mcFillLogCh(cs){
  var sel=mcI('mcLogCh'); if(!sel) return;
  var cur=sel.value, h='<option value="0">（引擎级）</option>';
  if(typeof mcSeqBuild==='function') mcSeqBuild(cs);
  for(var i=0;i<cs.length;i++){
    if(String(cs[i].mode||'NONE').toUpperCase()==='NONE') continue;
    h+='<option value="'+cs[i].n+'">'+((typeof mcSeq==='function')?mcSeq(cs[i].n):cs[i].n)+'. '+mcEsc(cs[i].mode)+'</option>';
  }
  sel.innerHTML=h;
  if(cur) sel.value=cur;
}
function mcPostJson(url, body, cb){
  fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
             body:JSON.stringify(body||{})})
    .then(function(r){ return r.json(); }).then(function(d){ cb(d); })
    .catch(function(e){ mcMsg('网络错误: '+e, false); });
}
function mcRestartCh(n){
  mcMsg('通道 '+n+' 正在重启…');
  mcPostJson('/api/mc/restart',{ch:n},function(d){
    var ok=!!(d&&d.ok);
    mcMsg(ok?('通道 '+n+' 已重启'):('失败：'+String((d&&(d.error||d.out))||'')), ok);
    if(ok) setTimeout(mcRefreshAll, 2500);
  });
}
function mcRestartEngine(){
  if(!confirm('重启 MMDVM-Multi 引擎与全部通道？约 20~40 秒。')) return;
  mcMsg('正在重启引擎…');
  mcPostJson('/api/mc/multi-restart',{},function(d){
    var ok=!!(d&&d.ok);
    mcMsg(ok?'引擎与全部通道已重启':('失败：'+String((d&&(d.error||d.out))||'')), ok);
    if(ok) setTimeout(mcRefreshAll, 6000);
  });
}
function mcLoadLog(){
  var ch=(mcI('mcLogCh')||{}).value||'0', svc=(mcI('mcLogSvc')||{}).value||'mmdvmhost',
      ln=(mcI('mcLogLines')||{}).value||'200';
  var box=mcI('mcLogBox'); if(box) box.textContent='加载中…';
  fetch('/api/mc/logs?ch='+encodeURIComponent(ch)+'&svc='+encodeURIComponent(svc)+'&lines='+encodeURIComponent(ln),
        {credentials:'same-origin'})
  .then(function(r){ return r.json(); })
  .then(function(d){
    if(box) box.textContent = (d&&(d.text||d.out)) || ('读取失败：'+String((d&&d.error)||''));
  }).catch(function(e){ if(box) box.textContent='网络错误: '+e; });
}
/* 本文件提供「射频模式配置」页诊断卡所需的监控函数（原独立监控页已并入该页） */

</script>



<style>
/* ARCH responsive + night contrast refinements */
body:not(.light){
  --card:#0a1623;--card-hi:#102235;--card-elev:#142a40;
  --border:rgba(125,211,252,.20);--text:#eef8ff;--muted:#a8bfd6;--dim:#7e9bb8;
}
body:not(.light) .card,
body:not(.light) .stat,
body:not(.light) .fld,
body:not(.light) .port-card,
body:not(.light) .db-card,
body:not(.light) .qso-item,
body:not(.light) .console,
body:not(.light) .sp{
  border-color:rgba(125,211,252,.18)!important;
  box-shadow:0 8px 28px rgba(0,0,0,.24),inset 0 1px 0 rgba(255,255,255,.035)!important;
}
body:not(.light) input, body:not(.light) select, body:not(.light) textarea{
  background:#081522!important;color:#edf8ff!important;border-color:#29465f!important;
}
.tg-srv-card{
  text-align:left;border-radius:10px;padding:11px 12px;cursor:pointer;
  background:var(--card2);border:1px solid var(--border);color:var(--text);
}
.tg-srv-card.is-selected{
  background:linear-gradient(135deg,rgba(34,211,238,.13),rgba(59,130,246,.06))!important;
  border-color:#22d3ee!important;box-shadow:0 0 0 1px rgba(34,211,238,.18);
}
body:not(.light) .tg-srv-card{
  background:#0b1b2a!important;border-color:#2a4a66!important;color:#eaf7ff!important;
}
body:not(.light) .tg-srv-card.is-selected{
  background:linear-gradient(135deg,#102e48,#0b1c2d)!important;
  border-color:#22d3ee!important;box-shadow:0 0 16px rgba(34,211,238,.18);
}
body:not(.light) .tg-srv-card.is-active:not(.is-selected){border-color:#22c55e!important;}
body:not(.light) .hint{color:var(--muted)!important;}
#tgTermTable{table-layout:fixed;min-width:760px}
#tgTermTable th:nth-child(1),#tgTermTable td:nth-child(1){width:82px}
#tgTermTable th:nth-child(2),#tgTermTable td:nth-child(2){width:86px}
#tgTermTable th:nth-child(3),#tgTermTable td:nth-child(3){width:92px}
#tgTermTable th:nth-child(4),#tgTermTable td:nth-child(4){width:92px}
#tgTermTable th:nth-child(5),#tgTermTable td:nth-child(5){width:92px}
#tgTermTable th:nth-child(6),#tgTermTable td:nth-child(6){width:190px}
#tgTermTable th:nth-child(7),#tgTermTable td:nth-child(7){width:72px}
@media(min-width:1600px){.content{padding-left:26px;padding-right:26px}#tgSlots{gap:16px!important}}
@media(min-width:2200px){.content{max-width:1960px;margin-left:auto;margin-right:auto}}
@media(max-width:1100px){ #tgSlots{grid-template-columns:repeat(2,minmax(0,1fr))!important}}
@media(max-width:700px){
  .content{padding:10px 8px 24px!important}
  #tgSlots{grid-template-columns:1fr!important}
  #tgSrvCards{grid-template-columns:1fr!important}
  #tgSrvForm{grid-template-columns:1fr!important}
  #tgSrvForm > div:last-child{grid-column:auto!important;justify-content:flex-start!important}
  .form-grid{grid-template-columns:1fr!important}
  .main-top{padding-left:8px!important;padding-right:8px!important}
}
</style>

<script id="arch-redesign-116-js">
/* ARCH Redesign 1.0.116 — micro-interactions */
(function(){
  "use strict";
  document.body.classList.add("arch-ui-v2");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var clock = document.getElementById("archClock");
  function tick(){
    if(clock) clock.textContent = new Date().toLocaleTimeString("zh-CN",{hour12:false});
  }
  tick(); setInterval(tick, 1000);
  function syncTopbar(){
    var active = document.querySelector(".sb-item.active");
    var el = document.getElementById("archModeLabel");
    if(el){
      var text = active ? (active.textContent || "").replace(/\s+/g," ").trim() : "设备控制台";
      el.textContent = text || "设备控制台";
    }
  }
  syncTopbar();
  var nav = document.querySelector(".sb-nav");
  if(nav && window.MutationObserver){
    new MutationObserver(syncTopbar).observe(nav,{subtree:true,attributes:true,attributeFilter:["class"]});
  }
  if(!reduce && "IntersectionObserver" in window){
    var obs = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if(e.isIntersecting){ e.target.classList.add("arch-in"); obs.unobserve(e.target); }
      });
    },{threshold:.08,rootMargin:"0px 0px -40px 0px"});
    document.querySelectorAll(".card,.stat,.mc-card,.mode-pill,.freq-strip").forEach(function(el){
      el.classList.add("arch-reveal"); obs.observe(el);
    });
  }
  if(window.MutationObserver){
    var pageObs = new MutationObserver(function(records){
      records.forEach(function(r){
        var el = r.target;
        if(el.classList && el.classList.contains("page") && el.style.display !== "none"){
          el.classList.remove("arch-page-enter");
          void el.offsetWidth;
          el.classList.add("arch-page-enter");
        }
      });
    });
    document.querySelectorAll(".page").forEach(function(el){ pageObs.observe(el,{attributes:true,attributeFilter:["style"]}); });
  }
  document.addEventListener("click", function(ev){
    var target = ev.target.closest && ev.target.closest("button,.btn,.mode-pill,.sb-item");
    if(!target || reduce) return;
    var rect = target.getBoundingClientRect();
    var d = Math.max(rect.width, rect.height);
    var ripple = document.createElement("span");
    ripple.className = "arch-ripple";
    ripple.style.width = ripple.style.height = d + "px";
    ripple.style.left = (ev.clientX - rect.left - d/2) + "px";
    ripple.style.top = (ev.clientY - rect.top - d/2) + "px";
    if(getComputedStyle(target).position === "static") target.style.position = "relative";
    target.appendChild(ripple);
    setTimeout(function(){ ripple.remove(); }, 700);
  }, true);
  window.ARCH_REDESIGN = {version:"1.0.118", syncTopbar:syncTopbar};
})();

</script>
</body>
</html>'''

@app.route('/')
def index():
    return render_template_string(HTML)

_TELE_CACHE = {'t': 0.0, 'light': None, 'payload': None}
_TELE_LOCK = threading.Lock()


@app.route('/api/power')
def api_power():
    """供电/降频状态（诊断接口；页面不再显示大横幅）。"""
    throttled = ''
    try:
        with open('/run/arch-power.state', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('throttled='):
                    throttled = line.split('=', 1)[1].strip()
                    break
    except Exception:
        pass
    clock = ''
    try:
        out = os.popen("vcgencmd measure_clock arm 2>/dev/null").read().strip()
        if '=' in out:
            clock = str(int(out.split('=')[1]) // 1000000)
    except Exception:
        pass
    warn = bool(throttled) and throttled not in ('0x0', '0', 'unknown')
    return jsonify({'ok': True, 'throttled': throttled, 'warn': warn, 'clock_mhz': clock})


@app.route('/api/status')
def api_status():
    light = request.args.get("light") == "1"
    ttl = 4.0 if light else 1.0
    now = time.time()
    if (_TELE_CACHE['payload'] is not None and _TELE_CACHE['light'] == light and
            now - _TELE_CACHE['t'] < ttl):
        return jsonify(_TELE_CACHE['payload'])
    with _TELE_LOCK:
        now = time.time()
        if (_TELE_CACHE['payload'] is not None and _TELE_CACHE['light'] == light and
                now - _TELE_CACHE['t'] < ttl):
            return jsonify(_TELE_CACHE['payload'])
        payload = get_telemetry(light)
        _TELE_CACHE['t'] = now
        _TELE_CACHE['light'] = light
        _TELE_CACHE['payload'] = payload
        return jsonify(payload)

@app.route('/api/ports')
def api_ports():
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', 'ports'])
    ports = {}
    if res['ok']:
        for line in res['stdout'].split('\n'):
            if '|' in line:
                parts = line.split('|')
                if len(parts) >= 3:
                    ports[parts[0]] = {
                        'proc': parts[1] if parts[1] != '-' else None,
                        'state': parts[2]
                    }
    return jsonify(ports)

@app.route('/api/switch/<mode>', methods=['POST'])
def api_switch(mode):
    allowed = {'DMR','D-STAR','YSF','P25','NXDN','FM','POCSAG'}
    if mode not in allowed:
        return jsonify({'success': False, 'message': '不支持的模式'}), 400
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', 'switch', mode], timeout=90)
    if res['ok']:
        # 只返回简短消息，绝不返回完整 stdout
        return jsonify({'success': True, 'message': '已切换到 ' + mode + ' 模式'})
    else:
        # 错误也截断到 200 字符
        err = (res['stderr'] or '切换失败').strip()[:200]
        return jsonify({'success': False, 'message': err}), 500

@app.route('/api/config/<mode>')
def api_config(mode):
    allowed = {'RF','DMR','D-STAR','YSF','P25','NXDN','FM','POCSAG'}
    if mode not in allowed:
        return jsonify({}), 400
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', 'get-config', mode])
    config = {}
    if res['ok']:
        cur_sec = None
        for raw in res['stdout'].split('\n'):
            line = raw.strip()
            if line.startswith('=== ') and line.endswith(' ==='):
                cur_sec = line.replace('=== ','').replace(' ===','')
                config[cur_sec] = {}
            elif '=' in line and cur_sec:
                k, v = line.split('=', 1)
                config[cur_sec][k] = v
    return jsonify(config)

def _cur_freqs():
    """实际频点优先读取通道规划，避免 Host 旧元数据把界面和电台带偏。"""
    _, item = plan_primary()
    if item:
        return str(item.get('rx') or ''), str(item.get('tx') or '')
    rx = tx = ''
    for p in ('/etc/MMDVM-Host.ini', '/etc/MMDVM-Host-1.ini'):
        try:
            cur = None
            for ln in open(p, encoding='utf-8', errors='replace'):
                s = ln.strip()
                if s.startswith('[') and s.endswith(']'):
                    cur = s[1:-1]
                elif cur == 'Modem' and s.lower().startswith('rxfrequency=') and not rx:
                    rx = s.split('=', 1)[1].strip()
                elif cur == 'Modem' and s.lower().startswith('txfrequency=') and not tx:
                    tx = s.split('=', 1)[1].strip()
            if rx and tx:
                break
        except Exception:
            pass
    if not rx or not tx:
        try:
            cur = None
            for ln in open('/etc/MMDVM-Multi.ini', encoding='utf-8', errors='replace'):
                s = ln.strip()
                if s.startswith('[') and s.endswith(']'):
                    cur = s[1:-1]
                elif cur == 'Modem' and s.lower().startswith('rxfrequency=') and not rx:
                    rx = s.split('=', 1)[1].strip()
                elif cur == 'Modem' and s.lower().startswith('txfrequency=') and not tx:
                    tx = s.split('=', 1)[1].strip()
        except Exception:
            pass
    return rx, tx


def _ini_get(path, sec, key, dv=''):
    """读取 ini 某段的键值（表单留空时用于"保持原值"）"""
    cur = None
    try:
        for ln in open(path, encoding='utf-8', errors='replace'):
            s = ln.strip()
            if s.startswith('[') and s.endswith(']'):
                cur = s[1:-1]
            elif cur == sec and '=' in s and not s.startswith(';') and not s.startswith('#'):
                k, v = s.split('=', 1)
                if k.strip().lower() == key.lower():
                    return v.strip()
    except Exception:
        pass
    return dv


def _flat_get(path, key, dv=''):
    try:
        for ln in open(path, encoding='utf-8', errors='replace'):
            s = ln.strip()
            if s and not s.startswith('#') and '=' in s:
                k, v = s.split('=', 1)
                if k.strip().lower() == key.lower():
                    return v.strip()
    except Exception:
        pass
    return dv


def _call_strip(v):
    """去掉 ircDDB 呼号尾缀（'NOCALL  G' -> 'NOCALL'）"""
    s = re.sub(r'\s+', ' ', str(v or '').strip()).upper()
    return re.sub(r'\s+[A-Z]$', '', s)


@app.route('/api/save/<mode>', methods=['POST'])
def api_save(mode):
    data = request.get_json() or {}
    cmd = ['sudo', '/usr/local/bin/web-manage.sh']

    # 频率兜底：RF / DMR / YSF 表单可能不含频率字段，避免 valid_freq 报错
    if mode in ('DMR', 'YSF', 'FM'):
        # 注意：RF 页已无频率输入 —— 不在这里补频率，避免把旧值当新值写回配置
        frx, ftx = _cur_freqs()
        if not data.get('rxfreq'):
            data['rxfreq'] = frx
        if not data.get('txfreq'):
            data['txfreq'] = ftx
    if mode == 'DMR':
        # 表单留空 -> 用当前配置兜底（修复"BM 主机不能为空"导致保存失败）
        if not data.get('bm_host'):
            data['bm_host'] = _ini_get('/etc/DMRGateway.ini', 'DMR Network 1', 'Address')
        if not data.get('bm_port'):
            data['bm_port'] = _ini_get('/etc/DMRGateway.ini', 'DMR Network 1', 'Port', '62031')
        if not data.get('bm_pw'):
            data['bm_pw'] = _ini_get('/etc/DMRGateway.ini', 'DMR Network 1', 'Password')
        if not data.get('id'):
            data['id'] = _ini_get('/etc/MMDVM-Host.ini', 'General', 'Id')
        if not data.get('callsign'):
            data['callsign'] = _ini_get('/etc/MMDVM-Host.ini', 'General', 'Callsign')
    if mode == 'D-STAR':
        if not data.get('gw_call'):
            data['gw_call'] = _call_strip(_flat_get('/etc/ircddbgateway', 'gatewayCallsign',
                                          _ini_get('/etc/MMDVM-Host.ini', 'General', 'Callsign')))
        if not data.get('rep_call'):
            data['rep_call'] = _call_strip(_flat_get('/etc/ircddbgateway', 'repeaterCall1',
                                           data.get('gw_call', '')))
        data['ircddb_host'] = data.get('ircddb_host') or _flat_get(
            '/etc/ircddbgateway', 'ircddbHostname', 'rr.ircddb.net')

    if mode == 'RF':
        data['duplex'] = '1'          # 本系统固定双工
        if not data.get('id'):
            data['id'] = ''

    if mode == 'RF':
        cmd += ['save-rf',
                data.get('callsign',''), data.get('id',''),
                data.get('rxfreq',''), data.get('txfreq',''),
                data.get('duplex','1'), data.get('power','1'),
                data.get('rxgain','50'), data.get('txgain','30')]
    elif mode == 'DMR':
        cmd += ['save-dmr',
                data.get('callsign',''), data.get('id',''),
                data.get('cc','1'), data.get('rxfreq',''), data.get('txfreq',''),
                data.get('bm_host',''), data.get('bm_port','62031'),
                data.get('bm_pw',''), data.get('essid',''), data.get('channels','')]
    elif mode == 'YSF':
        cmd += ['save-ysf',
                data.get('callsign',''), data.get('id',''),
                data.get('suffix','RPT') or 'RPT', data.get('startup',''),
                data.get('wiresx','1'), data.get('uppermode','')]
    elif mode == 'P25':
        cmd += ['save-p25',
                data.get('callsign',''), data.get('nac','293'),
                data.get('static',''), '120', '60']
    elif mode == 'NXDN':
        cmd += ['save-nxdn',
                data.get('callsign',''), data.get('id',''),
                data.get('ran','1'), data.get('protocol','Icom'), data.get('static',''),
                '120', '60']
    elif mode == 'D-STAR':
        # ★ 本机模块字母跟随“模块呼号”尾字母（NOCALL B -> B），没写尾字母则默认 B
        _rep_raw = str(data.get('rep_call','') or '').strip()
        _mb = re.search(r'([A-Za-z])\s*$', _rep_raw)
        _band = _mb.group(1).upper() if _mb else 'B'
        cmd += ['save-dstar',
                _call_strip(data.get('gw_call','')), _call_strip(_rep_raw),
                _band, data.get('irc_user',''), data.get('irc_pw',''),
                data.get('rxfreq',''), data.get('txfreq',''),
                data.get('reflector1',''),
                data.get('atStartup1','1'),
                data.get('callsign_routing',''),
                data.get('time_announce',''),
                data.get('use_dplus_xrf',''),
                data.get('ircddb_host','')]
    elif mode == 'FM':
        cmd += ['save-fm',
                data.get('callsign',''), data.get('ctcss','88.5'),
                '30', '20',
                data.get('rxfreq',''), data.get('txfreq','')]
    elif mode == 'POCSAG':
        cmd += ['save-pocsag', data.get('freq','439500000')]
    else:
        return jsonify({'success': False, 'message': 'NONE'}), 400

    res = run_cmd(cmd, timeout=60)
    msg = (res['stdout'] if res['ok'] else (res['stderr'] or '保存失败')).strip()
    # 失败时返回 500，前端才会走 "保存失败: xxx" 分支（原来恒返回 200 导致提示被吞）
    return jsonify({'success': bool(res['ok']), 'ok': bool(res['ok']),
                    'message': msg[:400]}), (200 if res['ok'] else 500)

@app.route('/api/bm-status')
def api_bm_status():
    """BrandMeister 会话状态：直接解析 DMRGateway 日志，给出人话原因"""
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', 'logs', 'dmrgateway'], timeout=20)
    raw = (res['stdout'] if res['ok'] else (res['stderr'] or '')) or ''
    lines = [l.strip() for l in raw.replace('\r', '').split('\n') if l.strip()]
    state, reason = 'unknown', '未取得 DMRGateway 日志（服务可能未运行）'
    for line in reversed(lines):
        low = line.lower()
        if 'logged into the master successfully' in low:
            state, reason = 'online', '已登录 BrandMeister Master'
            break
        if 'master is closing down' in low:
            state = 'closed'
            reason = ('BM 主动断开：配置报文里的呼号必须与 BM 注册呼号一致'
                      '（不能是 NOCALL），或该 ESSID/热点密码在 BM 自服务页面未登记')
            break
        if 'login to the master has failed' in low:
            state, reason = 'auth', 'BM 登录失败：热点连接密码不正确'
            break
        if 'timed out' in low:
            state, reason = 'timeout', '连接 BM 超时：网络不通或被防火墙拦截'
            break
        if ('could not lookup' in low) or ('unable to resolve' in low):
            state, reason = 'dns', 'BM 主机名解析失败'
            break
    info = {}
    try:
        r2 = run_cmd(['sudo', '/usr/local/libexec/mmdvm-save.py', 'BMINFO'], timeout=20)
        if r2['ok'] and r2['stdout'].strip():
            info = json.loads(r2['stdout'].strip())
    except Exception:
        info = {}
    info.update({'ok': True, 'state': state, 'reason': reason, 'tail': lines[-6:]})
    return jsonify(info)

@app.route('/api/dstar-flags')
def api_dstar_flags():
    """读取 D-STAR 三个网关开关的当前状态"""
    def flat_get(path, key, dv=''):
        try:
            for ln in open(path, encoding='utf-8', errors='replace'):
                s = ln.strip()
                if s.lower().startswith(key.lower() + '='):
                    return s.split('=', 1)[1].strip()
        except Exception:
            pass
        return dv
    ts = {}
    try:
        for ln in open('/etc/timeserver', encoding='utf-8', errors='replace'):
            s = ln.strip()
            if '=' in s and not s.startswith('#'):
                k, v = s.split('=', 1)
                ts[k.strip().lower()] = v.strip()
    except Exception:
        pass
    flags = {
        'callsign_routing': flat_get('/etc/ircddbgateway', 'ircddbEnabled', '0') or '0',
        'use_dplus_xrf': '1' if os.path.exists('/etc/hostfiles.nodextra') else '0',
        'time_announce': '0' if os.path.exists('/etc/timeserver.dissable') else '1',
        'timeserver_send': ''.join(ts.get('send' + c, '0') for c in 'ABCDE'),
        'timeserver_lang': ts.get('language', '0'),
        'timeserver_interval': ts.get('interval', '2'),
    }
    return jsonify(flags)


@app.route('/api/logs/<service>')
def api_logs(service):
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', 'logs', service], timeout=15)
    return jsonify({'logs': res['stdout'] if res['ok'] else res['stderr']})

@app.route('/api/bm-test/<host>')
def api_bm_test(host):
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', 'bm-test', host], timeout=20)
    try:
        return jsonify(json.loads(res['stdout']))
    except: return jsonify({'host': host, 'loss': 'N/A', 'rtt': 'N/A'})

@app.route('/api/diagnose')
def api_diagnose():
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', 'diagnose'], timeout=30)
    return jsonify({'output': res['stdout'] if res['ok'] else res['stderr']})

@app.route('/api/backup', methods=['POST'])
def api_backup():
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', 'backup'], timeout=45)
    return jsonify({'message': res['stdout'] if res['ok'] else res['stderr']})

@app.route('/api/update-db/<dbtype>', methods=['POST'])
def api_update_db(dbtype):
    allowed_db={'dmrid','hosts','all','ysfhosts','p25hosts','nxdnhosts','dstarhosts','dmrhosts'}
    if dbtype not in allowed_db:
        return jsonify({'ok':False,'message':'无效数据库类型'}),400
    res = run_cmd(['sudo', '/usr/local/bin/web-manage.sh', 'update-db', dbtype], timeout=360)
    msg = (res['stdout'] if res['ok'] else (res['stderr'] or '更新失败'))
    msg = re.sub(r'\x1b\[[0-9;]*m', '', msg or '')
    msg = msg.strip()
    if len(msg) > 2400:
        msg = msg[-2400:]
    return jsonify({'ok': bool(res['ok']), 'message': msg or '更新完成'})

_DMR_RESTART_LAST = 0.0
_DMR_RESTART_LOCK = threading.Lock()

@app.route('/api/restart/<service>', methods=['POST'])
def api_restart(service):
    allowed={'mmdvmhost','dmrgateway','ysfgateway','p25gateway','nxdngateway','ircddbgatewayd','mmdvm-iq','mmdvm-web','all'}
    if service not in allowed:
        return jsonify({'ok':False,'message':'无效服务'}),400
    global _DMR_RESTART_LAST
    if service == 'dmrgateway':
        with _DMR_RESTART_LOCK:
            now = time.time()
            if now - _DMR_RESTART_LAST < 15.0:
                return jsonify({'ok': True, 'message': 'DMRGateway 重连请求已在执行，请等待登录完成'})
            _DMR_RESTART_LAST = now
    res=run_cmd(['sudo','/usr/local/bin/web-manage.sh','restart',service],timeout=45)
    msg=(res['stdout'] if res['ok'] else (res['stderr'] or res['stdout'] or '重启失败')).strip()
    return jsonify({'ok':bool(res['ok']),'message':msg or (service+' 已提交重启')})

# DMR 协议网络白名单 / 其它协议黑名单（Pi-Star DMR_Hosts.txt 里混有 XLX 条目）
_DMR_NETS = {'BM', 'DMR', 'TGIF', 'FREEDMR', 'FD', 'DMR+', 'DMRPLUS', 'HB',
             'HBLINK', 'FREESTAR', 'SYSTEMX', 'DMRX', 'DMR-MARC', 'MARC',
             'DAS', 'MMDVM', 'BRANDMEISTER', 'HOTSPOT'}
_NON_DMR_NET = {'XLX', 'REF', 'XRF', 'DCS', 'CCS', 'FCS', 'YSF', 'P25',
                'NXDN', 'M17', 'DSTAR', 'D-STAR', 'WIRESX'}
_NON_DMR_HOST = ('ysf', 'fcs', 'p25', 'nxdn', 'dstar', 'd-star', 'ircddb')


def _is_bm_dmr(m, keep_xlx=False):
    """主机记录是否属于 DMR 协议网络

    · net 在 DMR 白名单里 -> 是
    · net 是 XLX/REF/XRF/DCS/CCS/FCS/YSF/P25/NXDN/M17 等 -> 默认不是
      （XLX 只会出现在 D-STAR 页面的反射器列表里）
    · 都没有时按域名判断，并排除本机伪主机（127.0.0.x / localhost）
    """
    if not isinstance(m, dict):
        return False
    net = str(m.get('net') or '').strip().upper()
    if net in _NON_DMR_NET:
        return bool(keep_xlx and net == 'XLX')
    if net in _DMR_NETS:
        return True
    host = str(m.get('host') or '').strip().lower()
    if not host or host.startswith('127.') or host in ('localhost', '0.0.0.0'):
        return False
    for bad in _NON_DMR_HOST:
        if bad in host:
            return False
    for good in ('brandmeister', 'dmr', 'hblink', 'tgif', 'freedmr',
                 'systemx', 'mmdvm'):
        if good in host:
            return True
    return False


@app.route('/api/bm-list')
def api_bm_list():
    """DMR 服务器列表（hosts-build.py 从 Pi-Star DMR_Hosts.txt 转换）

    · 默认只返回 DMR 协议网络（BrandMeister / TGIF / FreeDMR / DMR+ / HB …）
    · ?net=BM 只看某个网络；?all=1 才会连 XLX 一起返回
    · 返回顺序保持主机库顺序（4602 中国 -> 4501 韩国 -> 其余 BM -> 其它 DMR 网）
    """
    import json as _json
    try:
        with open('/usr/local/etc/hosts/DMR_Hosts.json', encoding='utf-8') as f:
            data = _json.load(f)
    except Exception:
        data = []
    want = (request.args.get('net') or '').strip().upper()
    show_all = (request.args.get('all') or '') in ('1', 'true', 'yes')
    masters, nets = [], {}
    for m in (data if isinstance(data, list) else []):
        if not _is_bm_dmr(m, keep_xlx=show_all):
            continue
        host = (m.get('host') or '').strip()
        if not host:
            continue
        net = (m.get('net') or 'BM').strip().upper()
        nets[net] = nets.get(net, 0) + 1
        if want and net != want:
            continue
        masters.append({
            'id': str(m.get('id', '')),
            'net': net,
            'label': m.get('label', '') or host,
            'region': m.get('region', ''),
            'country': m.get('country', ''),
            'host': host,
            'port': m.get('port', 62031),
            'pw': m.get('pw', ''),
        })
    return jsonify({'masters': masters, 'count': len(masters),
                    'nets': nets, 'total': sum(nets.values()),
                    'net': want})


@app.route('/api/dstar-reflectors')
def api_dstar_reflectors():
    """D-STAR 反射器列表（从 Pi-Star 官方 hosts 转换的 JSON 读取）"""
    import json as _json
    try:
        with open('/usr/local/etc/hosts/DSTAR_Reflectors.json', encoding='utf-8') as f:
            data = _json.load(f)
    except Exception:
        data = {"REF": [], "XRF": [], "DCS": [], "XLX": [], "CCS": []}
    return jsonify(data)


def _reflector_items(data, mode):
    """兼容网关官方 JSON 和旧版网页数组格式。"""
    rows = data.get('items') if isinstance(data, dict) else data
    if rows is None and isinstance(data, dict):
        rows = data.get('reflectors') or []
    if not isinstance(rows, list):
        return []
    out = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        rid = str(it.get('id') if it.get('id') is not None else it.get('designator') or '')
        host = it.get('host') or it.get('ipv4') or it.get('ipv6') or ''
        port = it.get('port') or ''
        if mode == 'YSF':
            country = str(it.get('country') or '')
            name = str(it.get('name') or '')
            if not rid:
                rid = str(it.get('designator') or '')
            if it.get('use_xx_prefix'):
                full_name = 'XX-' + name
            elif country and name:
                full_name = country + '-' + name
            else:
                full_name = name
            out.append({'id': rid, 'name': full_name,
                        'desc': it.get('description') or it.get('desc') or '',
                        'host': host, 'port': port, 'country': country})
        else:
            if not rid:
                rid = str(it.get('designator') or '')
            out.append({'id': rid, 'name': it.get('name') or (mode + '-' + rid),
                        'host': host, 'port': port})
    return out


@app.route('/api/ysf-hosts')
def api_ysf_hosts_list():
    import json as _json
    try:
        with open('/usr/local/etc/hosts/YSFHosts.json', encoding='utf-8') as f:
            items = _reflector_items(_json.load(f), 'YSF')
    except Exception:
        items = []
    return jsonify({'ok': True, 'items': items, 'count': len(items)})


@app.route('/api/dmr/channels')
def api_dmr_channels():
    try:
        plan = json.load(open('/etc/mmdvm/channels.json', encoding='utf-8'))
    except Exception:
        return {'items': []}
    items = []
    for x in plan.get('list') or []:
        if str(x.get('mode', '')).upper() != 'DMR':
            continue
        try:
            n = int(x.get('n') or 0)
        except Exception:
            continue
        path = '/etc/DMRGateway.ini' if n == 1 else '/etc/DMRGateway-%d.ini' % n
        if not os.path.exists(path):
            path = '/etc/DMRGateway.ini'
        callsign = _ini_get(path, 'General', 'Callsign')
        full_id = _ini_get(path, 'General', 'Id')
        base_id = full_id
        essid = ''
        if len(full_id) == 9:
            base_id = full_id[:7]
            essid = full_id[7:]
        items.append({'n': n,
                      'callsign': callsign,
                      'id': base_id,
                      'essid': essid,
                      'cc': _ini_get(path, 'Info', 'ColorCode', '1'),
                      'rx': x.get('rx', ''),
                      'tx': x.get('tx', ''),
                      'host': _ini_get(path, 'DMR Network 1', 'Address'),
                      'port': _ini_get(path, 'DMR Network 1', 'Port', '62031'),
                      'password': _ini_get(path, 'DMR Network 1', 'Password')})
    return {'items': items}


@app.route('/api/gateway-status/<mode>')
def api_gateway_status(mode):
    mode=(mode or '').lower()
    units={'dmr':'dmrgateway','ysf':'ysfgateway','p25':'p25gateway',
           'nxdn':'nxdngateway','dstar':'ircddbgatewayd','fm':'fmo-bridge'}
    if mode not in units:
        return jsonify({'ok':False,'error':'unsupported'}),404

    def _ini(section, key, path, first=False):
        try:
            cur=''
            for ln in open(path, encoding='utf-8', errors='replace'):
                t=ln.strip()
                if t.startswith('[') and t.endswith(']'):
                    cur=t[1:-1]
                    continue
                if cur==section and t.lower().startswith(key.lower()+'='):
                    v=t.split('=',1)[1].strip()
                    return v.split(',')[0].strip() if first else v
        except Exception:
            pass
        return ''

    def _flat(path, key):
        try:
            for ln in open(path, encoding='utf-8', errors='replace'):
                t=ln.strip()
                if t and not t.startswith('#') and '=' in t:
                    k,v=t.split('=',1)
                    if k.strip()==key:
                        return v.strip()
        except Exception:
            pass
        return ''

    def _tcp_established(host, port):
        if not host:
            return False
        try:
            ip=socket.gethostbyname(host)
            target=socket.inet_aton(ip)[::-1].hex().upper()
            want='%04X' % int(port or 0)
            for fn in ('/proc/net/tcp','/proc/net/tcp6'):
                try:
                    for ln in open(fn, encoding='ascii', errors='ignore'):
                        f=ln.split()
                        if len(f) < 4:
                            continue
                        rem=f[2].split(':')
                        if len(rem)==2 and rem[0].upper()==target and rem[1].upper()==want and f[3]=='01':
                            return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    server=''
    if mode=='dmr':
        server=_ini('DMR Network 1','Address','/etc/DMRGateway.ini')
    elif mode=='ysf':
        server=_ini('Network','Startup','/etc/YSFGateway.ini')
    elif mode=='p25':
        server=_ini('Network','Static','/etc/P25Gateway.ini',True)
    elif mode=='nxdn':
        server=_ini('Network','Static','/etc/NXDNGateway.ini',True)
    elif mode=='dstar':
        server=_flat('/etc/ircddbgateway','reflector1')
    elif mode=='fm':
        try:
            with open('/opt/fmo-bridge/config.json', encoding='utf-8') as f:
                cfg=json.load(f)
            fm=cfg.get('server') or {}
            server=str(fm.get('mqtt_host') or '')
        except Exception:
            server=''

    ar=run_cmd(['systemctl','is-active',units[mode]],timeout=8)
    active=(ar.get('stdout','').strip()=='active')

    cached={}
    try:
        with open('/run/arch-gateway-status.json', encoding='utf-8') as f:
            cached=(json.load(f).get(mode) or {})
    except Exception:
        pass

    state=str(cached.get('state') or '')
    detail=str(cached.get('detail') or '')
    if not server and cached.get('server'):
        server=str(cached.get('server'))
    connected=bool(cached.get('connected'))

    if mode=='fm':
        port=0
        try:
            with open('/opt/fmo-bridge/config.json', encoding='utf-8') as f:
                port=int(((json.load(f).get('server') or {}).get('mqtt_port')) or 1883)
        except Exception:
            port=1883
        connected=active and _tcp_established(server, port)
        if connected:
            state='connected'; detail='FMO MQTT 已连接'
        elif active:
            state='waiting'; detail='桥接服务已启动，正在连接 FMO'
        else:
            state='disconnected'; detail='桥接服务未运行'
    elif mode=='dstar':
        if not connected and active and server:
            state='active'; detail='网关已启动，等待反射器确认'
        if not active:
            state='disconnected'; detail='网关服务未运行'
    elif not state:
        state='waiting' if active else 'disconnected'
        detail='等待网关连接' if active else '网关服务未运行'

    return jsonify({'ok':True,'mode':mode.upper(),'unit':units[mode],'active':active,
                    'connected':connected or state=='connected','state':state,
                    'server':server or '', 'detail':detail})
@app.route('/api/p25-hosts')
def api_p25_hosts_list():
    import json as _json
    try:
        with open('/usr/local/etc/hosts/P25Hosts.json', encoding='utf-8') as f:
            items = _reflector_items(_json.load(f), 'P25')
    except Exception:
        items = []
    return jsonify({'ok': True, 'items': items, 'count': len(items)})


@app.route('/api/nxdn-hosts')
def api_nxdn_hosts_list():
    import json as _json
    try:
        with open('/usr/local/etc/hosts/NXDNHosts.json', encoding='utf-8') as f:
            items = _reflector_items(_json.load(f), 'NXDN')
    except Exception:
        items = []
    return jsonify({'ok': True, 'items': items, 'count': len(items)})


@app.route('/api/fcs-hosts')
def api_fcs_hosts_list():
    import json as _json
    try:
        with open('/usr/local/etc/hosts/FCSHosts.json', encoding='utf-8') as f:
            items = _json.load(f)
    except Exception:
        items = []
    return jsonify({'ok': True, 'items': items, 'count': len(items)})


@app.route('/api/hosts-status')
def api_hosts_status():
    """主机 / 反射器库统计（看板展示）"""
    import json as _json
    for p in ('/usr/local/etc/hosts/hosts-index.json',
              '/usr/local/etc/hosts-index.json'):
        try:
            with open(p, encoding='utf-8') as f:
                return jsonify(_json.load(f))
        except Exception:
            continue
    return jsonify({'built': '', 'counts': {}})


@app.route('/api/hosts-rebuild', methods=['POST'])
def api_hosts_rebuild():
    """从本地 .txt 主机文件重建全部 JSON 索引"""
    res = run_cmd(['sudo', '/usr/local/libexec/hosts-build.py', '-v'], timeout=180)
    return jsonify({'ok': res['ok'], 'message': res['stdout'] or res['stderr']})


@app.route('/api/clientlog', methods=['POST'])
def api_clientlog():
    """接收浏览器端 JS 错误 / 诊断信息（写入 /tmp/mmdvm-client.log，用于远程排查）"""
    import time as _t
    d = request.get_json(silent=True) or {}
    try:
        line = '%s | %-8s | %s | %s | line=%s col=%s | build=%s | %s | %s\n' % (
            _t.strftime('%F %T'), str(d.get('kind', '?'))[:12], str(d.get('msg', ''))[:300],
            str(d.get('src', ''))[-70:], str(d.get('line', ''))[:6], str(d.get('col', ''))[:6],
            str(d.get('build', ''))[:24], str(d.get('url', ''))[:100], str(d.get('ua', ''))[:110])
        with open('/tmp/mmdvm-client.log', 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass
    return jsonify({'ok': True})


# ==================== 系统设置（WiFi / 系统信息 / 电源 / 时区）====================
_SYS_HELPER = "/usr/local/bin/sys-manage.sh"


_URL_RE = re.compile(r'^https?://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]{0,200}$')


def _sys_args_ok(act, args):
    """动作参数白名单校验：SSID/密码允许任意可见字符（含中文），其余按类型收紧"""
    for a in args:
        if len(a) > 80:
            return False
        if re.search(r'[\x00-\x1f\x7f]', a):
            return False
        if a.startswith('-'):
            return False
    a0 = args[0] if args else ''
    if act in ('logs',):
        if not re.match(r'^[A-Za-z0-9@._-]{1,60}$', a0):
            return False
        if len(args) > 1 and args[1] and not re.match(r'^\d{1,4}$', args[1]):
            return False
    elif act == 'ping':
        if not re.match(r'^[A-Za-z0-9._:-]{1,60}$', a0):
            return False
    elif act == 'tz':
        if not re.match(r'^[A-Za-z_]+/[A-Za-z0-9_+/-]{1,40}$', a0):
            return False
    elif act == 'wifi-country':
        return False          # ★ 国家码已固定 CN，不再允许从接口传入
    elif act == 'factory-reset':
        if a0 not in ('', 'net'):
            return False
    elif act == 'net-info':
        if a0 and a0 not in ('eth0', 'wlan0'):
            return False
    elif act == 'net-set':
        if a0 not in ('eth0', 'wlan0'):
            return False
        if len(args) > 1 and args[1] and not re.match(r'^\d{1,3}(?:\.\d{1,3}){3}$', args[1]):
            return False
        if len(args) > 2 and args[2] and not re.match(r'^(?:\d{1,3}(?:\.\d{1,3}){3}|\d{1,2})$', args[2]):
            return False
        if len(args) > 3 and args[3] and not re.match(r'^\d{1,3}(?:\.\d{1,3}){3}$', args[3]):
            return False
        if len(args) > 4 and args[4] and not re.match(r'^[0-9., ]{0,64}$', args[4]):
            return False
        if len(args) > 5 and args[5] not in ('static', 'dhcp', ''):
            return False
    elif act == 'wifi-radio':
        if a0 not in ('on', 'off'):
            return False
    elif act in ('wifi-connect', 'wifi-hidden'):
        if not a0:
            return False
    elif act == 'wifi-priority':
        if not re.match(r'^-?\d{1,4}$', args[1] if len(args) > 1 else ''):
            return False
    elif act == 'wifi-autoconnect':
        if (args[1] if len(args) > 1 else '') not in ('yes', 'no'):
            return False
    elif act in ('fw-url', 'fw-check', 'fw-download'):
        if a0 and not _URL_RE.match(a0):
            return False
    elif act == 'fw-install':
        if a0 and not re.match(r'^/tmp/[A-Za-z0-9._-]{1,60}$', a0):
            return False
    return True


@app.route('/api/sys/fw/upload', methods=['POST'])
def api_sys_fw_upload():
    """从电脑上传固件安装包（保存到 /tmp/mmdvm-fw.pkg，再交给 fw-install 处理）"""
    import os as _os
    f = request.files.get('file')
    if f is None or not f.filename:
        return jsonify({'ok': False, 'message': '没有收到文件（请选择安装包）'})
    ext = _os.path.splitext(f.filename)[1].lower()
    if ext not in ('.zip', '.gz', '.tgz', '.tar', '.deb', '.xz', '.bin', '.img'):
        return jsonify({'ok': False, 'message': '只支持 .tar.gz / .tgz / .zip / .deb 等压缩包，当前是 ' + ext})
    dst = '/tmp/mmdvm-fw.pkg'
    try:
        f.save(dst)
        size = _os.path.getsize(dst)
    except Exception as e:
        return jsonify({'ok': False, 'message': '保存失败：' + str(e)})
    return jsonify({'ok': True, 'path': dst, 'size': size,
                    'message': '已上传 %s（%.2f MB）→ %s，可点「安装已上传/已下载的包」' % (f.filename, size / 1048576.0, dst)})


def _sys_call(*args, timeout=90):
    try:
        r = subprocess.run(["sudo", "-n", _SYS_HELPER] + [str(a) for a in args],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _sys_spawn(*args):
    try:
        log = open('/tmp/arch-wifi-handoff.log', 'ab', buffering=0)
        subprocess.Popen(["sudo", "-n", _SYS_HELPER] + [str(a) for a in args],
                         stdout=log, stderr=log, start_new_session=True)
        log.close()
        return True, ""
    except Exception as e:
        return False, str(e)


@app.route('/api/sys/info')
def api_sys_info():
    rc, out, err = _sys_call('info', timeout=30)
    data = {}
    for ln in (out or '').splitlines():
        if '=' in ln:
            k, v = ln.split('=', 1)
            data[k.strip()] = v.strip()
    data['ok'] = (rc == 0)
    if err:
        data['error'] = err
    return jsonify(data)


@app.route('/api/sys/net')
def api_sys_net():
    rc, out, err = _sys_call('net', timeout=40)
    return (out or err or '读取失败'), 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route('/api/sys/wifi')
def api_sys_wifi():
    rc, out, err = _sys_call('wifi-list', timeout=60)
    items = []
    for ln in (out or '').splitlines():
        f = ln.split(':')
        if len(f) >= 3 and f[1].strip():
            items.append({'use': f[0].strip() == '*', 'ssid': f[1].strip(),
                          'signal': f[2].strip(), 'sec': (f[3].strip() if len(f) > 3 else '')})
    return jsonify({'ok': rc == 0, 'items': items, 'message': (err or '')})


@app.route('/api/sys/wifi/connect', methods=['POST'])
def api_sys_wifi_connect():
    ssid = (request.form.get('ssid') or '').strip()
    pw = request.form.get('password') or ''
    hid = '1' if (request.form.get('hidden') or '') in ('1', 'true', 'on', 'yes') else '0'
    if not ssid:
        return jsonify({'ok': False, 'message': '缺少 SSID'})
    if len(ssid) > 80 or len(pw) > 80:
        return jsonify({'ok': False, 'message': 'SSID 或密码过长'})
    ok, err = _sys_spawn('wifi-handoff', ssid, pw, hid)
    if not ok:
        return jsonify({'ok': False, 'message': '后台切换任务启动失败：' + err})
    return jsonify({'ok': True, 'scheduled': True,
                    'message': '配置已保存。设备将在约 5 秒后切换到 WiFi「%s」；ARCH多模网关会断开。请把手机连接到同一 WiFi，然后访问 http://ARCH.local 或路由器分配给设备的新 IP。' % ssid})


@app.route('/api/sys/action', methods=['POST'])
def api_sys_action():
    d = request.get_json(silent=True) or {}
    act = str(d.get('action') or '').strip()
    ALLOW = {'restart-hotspot': '热点服务已重启（约 30 秒后恢复）',
             'restart-web': 'Web 控制台已重启',
             'wifi-disconnect': '已断开 WiFi',
             'reboot': '设备即将重启，请 1 分钟后重新连接',
             'shutdown': '设备即将关机，需要手动上电'}
    # ★ 带参数的动作：act -> 允许的参数个数（参数只能是字面量，防注入）
    ARGED = {'time-status': 0, 'ntp': 1, 'tz': 1, 'timesync': 0,
             'sched-reboot': 3, 'backup': 0, 'logs': 2, 'ping': 1,
             # ---- WiFi ----
             'wifi-status': 0, 'wifi-scan': 0, 'wifi-saved': 0, 'wifi-radio': 1,
             'wifi-country': 1, 'wifi-connect': 3, 'wifi-hidden': 3,
             'wifi-forget': 1, 'wifi-reconnect': 1, 'wifi-info': 1,
             'wifi-priority': 2, 'wifi-autoconnect': 2,
             # ---- 树莓派系统更新 / ARCH固件更新 ----
             'sys-upgrade-count': 0, 'sys-update-check': 0, 'sys-update': 0,
             'sys-update-status': 0, 'sys-update-clean': 0,
             'boot-fw-status': 0, 'boot-fw-update': 0,
             'fw-info': 0, 'fw-url': 1, 'fw-check': 1, 'fw-download': 1,
             'fw-install': 1, 'fw-log': 1, 'fw-online-update': 0,
             'hotspot-update': 0, 'hotspot-update-status': 0, 'hotspot-heal': 0, 'perf-info': 0, 'update-status': 0,
             # ---- 网络参数 / WiFi 自愈 / 单元修正 ----
             'net-info': 1, 'net-set': 6, 'wifi-restore': 0, 'wifi-boot': 0, 'fix-units': 0,
             # ---- one-click factory reset ----
             'factory-reset': 1}
    args = []
    if act in ARGED:
        raw = d.get('args') or []
        if isinstance(raw, str):
            raw = raw.split('\x1f')
        args = [str(x) for x in raw][:ARGED[act]]
        while len(args) < ARGED[act]:
            args.append('')
        if not _sys_args_ok(act, args):
            return jsonify({'ok': False, 'message': '参数非法'})
    elif act not in ALLOW:
        return jsonify({'ok': False, 'message': '不支持的操作'})
    rc, out, err = _sys_call(act, *args, timeout=180)
    # 重启 Web / 重启 / 关机 都会切断本次请求的子进程，返回码不可靠，视为已下发
    ok = (rc == 0) or act in ('reboot', 'shutdown', 'restart-web')
    if act in ARGED:
        # 这类动作把助手原始输出直接回显（日志/诊断/备份等）
        return jsonify({'ok': ok, 'message': (out or err or ''), 'rc': rc})
    msg = ALLOW[act]
    if out:
        msg += ' | ' + out
    elif err:
        msg += ' | ' + err
    return jsonify({'ok': ok, 'message': msg})


# ==================== 多信道扩展（MMDVM-Multi）====================
# 复用本控制台进程与端口：仅追加 /multi 页面 + /api/mc/* 接口，不改动原有页面

_MC_HELPER = "/usr/local/bin/web-multi.sh"


def _mc_call(*args, timeout=90):
    try:
        r = subprocess.run(["sudo", "-n", _MC_HELPER] + [str(a) for a in args],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _mc_reply(*args, timeout=90):
    rc, out, err = _mc_call(*args, timeout=timeout)
    try:
        d = json.loads(out) if out else {}
        if not isinstance(d, dict):
            d = {"ok": rc == 0, "out": out}
    except Exception:
        d = {"ok": rc == 0, "out": out}
    if "ok" not in d:
        d["ok"] = rc == 0
    if err:
        d["stderr"] = err
        if not d.get("error"):
            d["error"] = err
        if not d.get("out"):
            d["out"] = err
    return jsonify(d), (200 if d.get("ok") else 500)


MC_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARCH 多信道控制台</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1220;color:#dfe8f5;font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif;padding:14px}
body.embed{padding:6px 10px 10px}
body.embed .top{display:none}
a{color:#4fc3f7;text-decoration:none}
.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.top h1{font-size:18px;font-weight:650;letter-spacing:.3px}
.badge{font-size:12px;padding:3px 9px;border-radius:99px;background:#17233a;border:1px solid #26354f;color:#8fa6c4}
.badge.ok{background:#0e2f22;border-color:#1d6b4a;color:#4ade80}
.badge.bad{background:#331616;border-color:#7f2222;color:#f87171}
.spacer{flex:1}
button{cursor:pointer;border:1px solid #2a3a56;background:#16264a;color:#cfe0f5;padding:6px 12px;border-radius:7px;font-size:14px;font-family:inherit}
button:hover{background:#1d3260;border-color:#3b5686}
button:active{transform:translateY(1px)}
button.sm{padding:4px 9px;font-size:12px}
button.dngr{background:#3a1a1a;border-color:#6e2626;color:#fca5a5}
.card{background:#101a2e;border:1px solid #1e2b44;border-radius:11px;padding:14px;margin-bottom:14px}
.card>h2{font-size:14px;font-weight:640;color:#9fb6d6;margin-bottom:11px;letter-spacing:.3px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:10px}
.kv{background:#0c1526;border:1px solid #1b2840;border-radius:8px;padding:9px 11px}
.kv .k{font-size:12.5px;color:#7d93b2;letter-spacing:.4px;text-transform:uppercase}
.kv .v{font-size:16px;font-weight:600;margin-top:3px;word-break:break-all}
.chs{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px;margin-bottom:14px}
.ch{background:#101a2e;border:1px solid #1e2b44;border-radius:11px;overflow:hidden}
.ch-hd{display:flex;align-items:center;gap:9px;padding:11px 14px;background:linear-gradient(180deg,#17233a,#111c30);border-bottom:1px solid #1e2b44}
.ch-hd .num{font-size:16px;font-weight:680}
.ch-hd .mode{font-size:12px;font-weight:700;padding:3px 9px;border-radius:6px;background:#1b3050;color:#7dd3fc;letter-spacing:.5px}
.ch-bd{padding:13px 14px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.row+.row{margin-top:9px}
select{background:#0c1526;border:1px solid #2a3a56;color:#dfe8f5;padding:6px 9px;border-radius:7px;font-size:14px;font-family:inherit}
.dot{width:9px;height:9px;border-radius:99px;background:#4b5563;display:inline-block;flex-shrink:0}
.dot.on{background:#22c55e;box-shadow:0 0 7px #22c55e}
.dot.off{background:#ef4444;box-shadow:0 0 7px #ef4444}
.st{display:flex;align-items:center;gap:6px;font-size:13.5px;color:#9fb6d6}
.qso{margin-top:10px;padding:8px 10px;background:#0c1526;border:1px solid #1b2840;border-radius:8px;font-size:13.5px;color:#9fb6d6;min-height:34px}
.svcs{display:flex;flex-wrap:wrap;gap:8px}
.svc{display:flex;align-items:center;gap:7px;font-size:12.5px;background:#0c1526;border:1px solid #1b2840;border-radius:8px;padding:6px 10px}
/* ---- 信道规划器 ---- */
.pl{background:linear-gradient(165deg,#111d33,#0b1322);border:1px solid #1f304d;border-radius:12px;padding:15px;margin-bottom:14px;position:relative;overflow:hidden}
.pl:before{content:"";position:absolute;inset:0 0 auto 0;height:2px;background:linear-gradient(90deg,#22d3ee,#3b82f6,#8b5cf6,transparent)}
.pl h2{font-size:14px;font-weight:660;color:#9fb6d6;margin-bottom:13px;display:flex;align-items:center;gap:8px}
.pl h2 .tag{font-size:12px;font-weight:700;letter-spacing:1.2px;color:#22d3ee;background:#0a2733;border:1px solid #155e75;border-radius:5px;padding:2px 7px}
.chips{display:flex;gap:7px;flex-wrap:wrap}
.chip{cursor:pointer;min-width:40px;text-align:center;padding:7px 13px;border-radius:8px;border:1px solid #2a3a56;background:#0c1526;color:#9fb6d6;font-size:14px;font-weight:650;user-select:none;transition:.14s}
.chip:hover{border-color:#3b5686;color:#cfe0f5}
.chip.on{background:linear-gradient(180deg,#0e7490,#155e75);border-color:#22d3ee;color:#e0f7ff;box-shadow:0 0 12px rgba(34,211,238,.28)}
.plrow{display:grid;grid-template-columns:64px 132px 1fr 1fr;gap:9px;align-items:center;padding:8px 10px;background:#0a1322;border:1px solid #1b2840;border-radius:9px;margin-top:8px}
.plrow .n{font-weight:700;color:#7dd3fc;font-size:14.5px;letter-spacing:.3px}
.plrow.dis{opacity:.34}
.plout{margin-top:12px;padding:11px 13px;background:#070d18;border:1px solid #1b2840;border-radius:9px;font:12.5px/1.6 Consolas,monospace;white-space:pre-wrap;word-break:break-all;color:#9fd8b4;max-height:250px;overflow:auto;display:none}
.lbl{font-size:12.5px;color:#7d93b2;letter-spacing:.5px;text-transform:uppercase;margin-bottom:4px}
.f{display:flex;flex-direction:column}
input[type=text],input[type=number]{background:#0c1526;border:1px solid #2a3a56;color:#dfe8f5;padding:7px 10px;border-radius:7px;font-size:14.5px;font-family:inherit;width:100%}
input[type=text]:focus,input[type=number]:focus{outline:none;border-color:#22d3ee;box-shadow:0 0 0 2px rgba(34,211,238,.14)}
input[type=number]{-moz-appearance:textfield}
pre{background:#070d18;border:1px solid #1b2840;border-radius:8px;padding:12px;max-height:430px;overflow:auto;font:12.5px/1.5 Consolas,monospace;white-space:pre-wrap;word-break:break-all;color:#b9cbe4}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:99;display:none;padding:9px 14px;border-radius:11px;background:rgba(13,21,32,.74);border:1px solid rgba(var(--theme-rgb),.42);color:#eaf2ff;font-size:13.5px;box-shadow:0 10px 28px rgba(0,0,0,.35);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);max-width:min(420px,84vw);text-align:center}
#toast.ok{background:#0e2f22;border-color:#1d6b4a;color:#a7f3d0}
#toast.bad{background:#331616;border-color:#7f2222;color:#fecaca}
/* ==================== ★ 整体字号放大 0.5 号 ==================== */
body{font:14.5px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}
.top h1{font-size:18.5px}
.badge{font-size:13.5px}
button{font-size:13.5px}
button.sm{font-size:13.5px}
.card>h2{font-size:14.5px}
.kv .k{font-size:12px}
.kv .v{font-size:15.5px}
.ch-hd .num{font-size:16.5px}
.ch-hd .mode{font-size:13.5px}
.lbl{font-size:12px}
.plrow{grid-template-columns:96px 132px 1fr 1fr}
.plrow .n{font-size:14px}
.plrow .fq{font-size:13px}
input[type=text],input[type=number]{font-size:14px}
select{font-size:13.5px}
.plout,pre{font-size:13px}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:99;display:none;padding:9px 14px;border-radius:11px;background:rgba(13,21,32,.74);border:1px solid rgba(var(--theme-rgb),.42);color:#eaf2ff;font-size:13.5px;box-shadow:0 10px 28px rgba(0,0,0,.35);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);max-width:min(420px,84vw);text-align:center}

/* ================================================================
   UNIFIED TYPOGRAPHY & SPACING SYSTEM
   ================================================================ */
:root {
  /* Type scale */
  --fs-xs:10.5px;
  --fs-sm:11.5px;
  --fs-base:13px;
  --fs-md:14px;
  --fs-lg:16px;
  --fs-xl:18px;
  --fs-2xl:22px;

  /* Spacing scale */
  --sp-1:4px;
  --sp-2:8px;
  --sp-3:12px;
  --sp-4:16px;
  --sp-5:20px;
  --sp-6:24px;

  /* Radius */
  --r-sm:6px;
  --r-md:8px;
  --r-lg:11px;
  --r-xl:13px;
}

/* --- Unified card padding --- */
.card { padding:var(--sp-4) var(--sp-5); }

/* --- Unified form field spacing --- */
.form-grid .fld { gap:6px; }
.form-grid label { margin-bottom:0; }

/* --- Unified section titles --- */
.freq-banner-txt > b,
.card-title,
.mc-hd {
  letter-spacing:.2px;
}

/* --- Consistent mono font sizing --- */
.stat-val,
.freq-chip,
.mc-freq {
  font-variant-numeric:tabular-nums;
}

/* --- Better line heights --- */
.card-sub,
.db-card p,
.form-grid .hint,
.essid-note,
.freq-strip {
  line-height:1.6;
}

/* --- Unified border radius --- */
.card { border-radius:var(--r-lg); }
.btn { border-radius:var(--r-md); }
.mode-pill { border-radius:var(--r-md); }
.mc-card { border-radius:var(--r-lg); }
.qso-item { border-radius:var(--r-xl); }
.stat { border-radius:var(--r-lg); }
.port-card { border-radius:var(--r-md); }
.db-card { border-radius:var(--r-lg); }
.freq-banner { border-radius:var(--r-lg); }
.preset-box { border-radius:var(--r-lg); }
.sp { border-radius:var(--r-xl); }
.bm-bar { border-radius:var(--r-md); }
.console { border-radius:var(--r-md); }
.freq-chip { border-radius:var(--r-md); }
.freq-row { border-radius:var(--r-md); }
.freq-strip { border-radius:var(--r-md); }
.db-bar { border-radius:var(--r-lg); }
.port-badge { border-radius:14px; }

/* --- Unified transition timing --- */
.card,
.btn,
.mode-pill,
.sb-item,
.port-card,
.db-card,
.qso-item,
.freq-chip,
.sp-chip,
.sp-row,
select,
input {
  transition:all .2s cubic-bezier(.4,0,.2,1);
}

/* --- Light mode: softer body background gradient --- */
body.light {
  background:
    radial-gradient(1200px 600px at 12% -10%, rgba(16,185,129,.03), transparent 60%),
    radial-gradient(900px 500px at 88% 0%, rgba(56,189,248,.03), transparent 55%),
    #e8e3d9 !important;
  background-attachment:fixed !important;
}
body.light::before {
  background:
    linear-gradient(rgba(0,0,0,.015) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0,0,0,.015) 1px, transparent 1px) !important;
  background-size:44px 44px !important;
  mask-image:radial-gradient(circle at 50% 0%, #000 0%, transparent 78%) !important;
  -webkit-mask-image:radial-gradient(circle at 50% 0%, #000 0%, transparent 78%) !important;
}
body.light::after {
  display:none !important;
}


/* ========== FINAL CONTRAST OVERRIDE ========== */
body.light * {
  --muted:#4b5563;
  --dim:#6b7280;
}
body.light [style*="color:var(--muted)"] { color:#4b5563 !important; }
body.light [style*="color:var(--dim)"] { color:#6b7280 !important; }

</style>
</head>
<body>
<div class="top">
  <h1>ARCH 多信道控制台</h1>
  <span class="badge" id="bEngine">引擎 --</span>
  <span class="badge" id="bCh">信道 --</span>
  <span class="spacer"></span>
  <span class="badge" id="bTime">--</span>
  <button onclick="refresh()">立即刷新</button>
  <button class="sm" onclick="multiRestart()">重启 Multi 引擎</button>
  <a href="/" class="badge">← 返回主控制台</a>
</div>

<div class="pl">
  <h2><span class="tag">PLANNER</span>信道规划 · 自由选择通道数与各通道模式</h2>

  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:12px">
    <div class="f"><div class="lbl">呼号 Callsign</div>
      <input type="text" id="pCall" placeholder="NOCALL" maxlength="10"></div>
    <div class="f"><div class="lbl">DMR ID</div>
      <input type="text" id="pId" placeholder="000000000" maxlength="12"></div>
  </div>

  <div class="lbl">实际运行的通道数量（1 — 7）</div>
  <div class="chips" id="pChips" style="margin-bottom:12px"></div>

  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin:4px 0 12px">
    <div class="f"><div class="lbl">RX 中心频率（Hz，SDR 基准 = 通道 1）</div>
      <input type="number" id="pCrx" step="25000" value="434500000" oninput="planFreqs()"></div>
    <div class="f"><div class="lbl">TX 中心频率（Hz）</div>
      <input type="number" id="pCtx" step="25000" value="439500000" oninput="planFreqs()"></div>
  </div>

  <div class="lbl">各通道模式与频率（Hz）—— 频率可以直接修改</div>
  <div id="pRows"></div>

  <div class="lbl" style="margin-top:10px;line-height:1.75;text-transform:none;color:#7d93b2">
    ⓘ 多路射频同时工作，不需要切换：每路各自跑自己的模式。<br>
    ⓘ 频率可以自由修改：改任意一路会以「它」为准整体平移（因为 SX1255 硬件各通道固定相隔 25 kHz）。<br>
    ⓘ 例如把通道 2 改成 434.5375，中心会自动变成 434.5125，通道 1/3/4 跟着变 434.5125/434.5625/434.5875。<br>
    ⓘ 表格里显示的 MHz 就是设备真实的收发频率，对讲机请按它设置。
  </div>

  <div class="row" style="margin-top:13px">
    <button onclick="planLoad()">读取当前规划</button>
    <button onclick="planApply()" style="background:#0e7490;border-color:#22d3ee;color:#e0f7ff">应用规划（写入配置并重启）</button>
    <button onclick="planSave()">仅保存规划</button>
  </div>
  <div class="plout" id="pOut"></div>
</div>

<div class="card">
  <h2>多信道引擎（SX1255 共享 I/Q 前端）</h2>
  <div class="grid" id="eng">加载中...</div>
</div>

<div class="chs" id="chs"></div>

<div class="card">
  <h2>服务状态</h2>
  <div class="svcs" id="svcs">加载中...</div>
</div>

<div class="card">
  <h2>日志</h2>
  <div class="row">
    <select id="logCh"></select>
    <select id="logSvc">
      <option value="mmdvmhost">MMDVM-Host</option>
      <option value="mmdvm-iq">MMDVM-IQ</option>
      <option value="mmdvm-multi">ARCH 融合中枢</option>
      <option value="dmrgateway">DMRGateway</option>
      <option value="ircddbgatewayd">ircDDBGateway (D-STAR)</option>
      <option value="ysfgateway">YSFGateway</option>
      <option value="p25gateway">P25Gateway</option>
      <option value="nxdngateway">NXDNGateway</option>
    </select>
    <select id="logLines">
      <option value="100">100 行</option>
      <option value="200" selected>200 行</option>
      <option value="500">500 行</option>
    </select>
    <button onclick="loadLog()">加载日志</button>
  </div>
  <pre id="logBox">点击“加载日志”查看。</pre>
</div>

<div id="toast"></div>
<script>
if(location.search.indexOf('embed=1')>=0)document.body.className='embed';
function $(i){return document.getElementById(i);}
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function dot(on){return '<span class="dot '+(on?"on":"off")+'"></span>';}
function kv(k,v){return '<div class="kv"><div class="k">'+esc(k)+'</div><div class="v">'+esc(v)+'</div></div>';}
function toast(m,ok){
  var t=$("toast"); t.textContent=m; t.className=(ok===false?"bad":(ok===true?"ok":""));
  t.style.display="block"; clearTimeout(window._tt);
  window._tt=setTimeout(function(){t.style.display="none";},4200);
}
var MODES=["DMR","D-STAR","YSF","P25","NXDN","FM","POCSAG","NONE"];

function post(u,b){
  return fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})}).then(function(r){return r.json();});
}
function refresh(){
  fetch("/api/mc/channels?light=1").then(function(r){return r.json();}).then(function(d){
    if(!d||d.ok===false){
      $("bEngine").textContent="读取失败"; $("bEngine").className="badge bad";
      return;
    }
    render(d);
    $("bTime").textContent=new Date().toLocaleTimeString();
  }).catch(function(e){ toast("网络错误: "+e,false); });
}
function render(d){
  var e=d.multi||{};
  $("bEngine").textContent = e.active ? "引擎运行中" : "引擎停止";
  $("bEngine").className = "badge "+(e.active?"ok":"bad");
  $("bCh").textContent = "信道 "+(d.channels||[]).length+" / 配置 "+e.channels;
  $("eng").innerHTML = kv("状态", e.active?"运行":"停止")+kv("信道数", e.channels)
    +kv("采样率", e.rate?e.rate+" Hz":"--")+kv("SDR 类型", e.sdr||"--")
    +kv("RX 中心", e.rx?e.rx+" Hz":"--")+kv("TX 中心", e.tx?e.tx+" Hz":"--")
    +kv("RX 增益", e.rxg==null?"--":e.rxg)+kv("TX 增益", e.txg==null?"--":e.txg);

  var cs=d.channels||[], h="";
  for(var i=0;i<cs.length;i++){
    var c=cs[i];
    h+='<div class="ch"><div class="ch-hd"><span class="num">射频通道 '+mcSeq(c.n)+'</span>'
      +'<span class="mode">'+esc(c.mode)+'</span><span class="spacer"></span>'
      +dot(c.iq)+'<span class="st">IQ</span>'+dot(c.host)+'<span class="st">Host</span></div>'
      +'<div class="ch-bd">'
      +'<div class="grid" style="grid-template-columns:1fr 1fr">'
        +kv("RX 频率", c.rx+" Hz")+kv("TX 频率", c.tx+" Hz")
        +kv("Multi 端口", c.mport)+kv("Host 端口", c.hport)
      +'</div>'
      +'<div class="row" style="margin-top:11px">'
        +'<span class="lbl" style="text-transform:none">模式由「信道规划」分配；'
        +((PLN&&PLN.channels)||MC_SEQ_CHS.length||1)+' 路同时工作，无需切换</span>'
        +'<button class="sm" onclick="restartCh('+c.n+')">重启本通道</button>'
      +'</div>'
      +'<div class="qso">最近通信: '+esc(c.lastq||"（无）")+'</div>'
      +'</div></div>';
  }
  $("chs").innerHTML = h || '<div class="card">未检测到信道配置</div>';

  var sv=d.services||[], sh="";
  for(var k=0;k<sv.length;k++){ sh+='<div class="svc">'+dot(sv[k].active)+esc(sv[k].name)+'</div>'; }
  $("svcs").innerHTML=sh||"无";
  fillLogCh(cs);
}
function fillLogCh(cs){
  var sel=$("logCh"), cur=sel.value, h='<option value="0">（引擎级）</option>';
  mcSeqBuild(cs);
  for(var i=0;i<cs.length;i++){
    if(String(cs[i].mode||'NONE').toUpperCase()==='NONE') continue;
    h+='<option value="'+cs[i].n+'">'+mcSeq(cs[i].n)+'. '+esc(cs[i].mode)+'</option>';
  }
  sel.innerHTML=h;
  if(cur){ sel.value=cur; }
}
function restartCh(n){
  toast("通道 "+n+" 正在重启 ...");
  post("/api/mc/restart",{ch:n}).then(function(r){
    toast(r.ok?("通道 "+n+" 已重启"):("失败: "+(r.error||r.out)),r.ok);
    setTimeout(refresh,1800);
  }).catch(function(e){ toast("错误: "+e,false); });
}
function multiRestart(){
  toast("正在重启 MMDVM-Multi 引擎 ...");
  post("/api/mc/multi-restart",{}).then(function(r){
    toast(r.ok?"MMDVM-Multi 已重启":"失败: "+(r.error||r.out),r.ok);
    setTimeout(refresh,3000);
  }).catch(function(e){ toast("错误: "+e,false); });
}
function loadLog(){
  var ch=$("logCh").value, svc=$("logSvc").value, ln=$("logLines").value;
  $("logBox").textContent="加载中...";
  fetch("/api/mc/logs?ch="+ch+"&svc="+svc+"&lines="+ln).then(function(r){return r.json();}).then(function(d){
    $("logBox").textContent = d.text || ("> (无输出) "+(d.error||""));
  }).catch(function(e){ $("logBox").textContent="错误: "+e; });
}
/* ==================== 信道规划器 ==================== */
var PLN={channels:2,callsign:"",dmr_id:"",center_rx:434500000,center_tx:439500000,spacing:25000,sample_rate:150000,list:[]};
function pv(id,v){var e=$(id); if(v!==undefined&&v!==null&&v!==""){e.value=v;} return e.value;}
function pnum(id,dv){var v=parseFloat($(id).value); return isNaN(v)?dv:v;}
function planGet(i){
  for(var k=0;k<PLN.list.length;k++){ if(PLN.list[k].n===i){ return PLN.list[k]; } }
  var sp=25000, crx=434500000, ctx=439500000;
  var st=chGridStep(i);
  return {n:i,mode:"NONE",rx:crx+st*sp,tx:ctx+st*sp};
}
function planRender(n){
  if(n){ PLN.channels=n; }
  n=PLN.channels;
  var ch="";
  for(var i=1;i<=MC_DYN_CH();i++){   /* MC_DYN_CH 动态通道数 */
    ch+='<div class="chip'+(i===n?" on":"")+'" onclick="planRender('+i+')">'+i+'</div>';
  }
  $("pChips").innerHTML=ch;
  var h="";
  for(var i=1;i<=MC_DYN_CH();i++){   /* MC_DYN_CH 动态通道数 */
    var c=planGet(i), opts="", dis=(i>n);
    for(var j=0;j<MODES.length;j++){
      opts+='<option value="'+MODES[j]+'"'+(c.mode===MODES[j]?" selected":"")+'>'+MODES[j]+'</option>';
    }
    h+='<div class="plrow'+(dis?" dis":"")+'"><span class="n">射频通道 '+i+'</span>'
      +'<select id="pm'+i+'"'+(dis?" disabled":"")+'>'+opts+'</select>'
      +'<input type="number" id="pr'+i+'" step="2500" '+(dis?"disabled":"")+' oninput="onChFreq('+i+')">'
      +'<input type="number" id="pt'+i+'" step="2500" '+(dis?"disabled":"")+' oninput="onChFreq('+i+')">'
      +'<span class="fq">RX <b id="fr'+i+'">--</b> ／ TX <b id="ft'+i+'">--</b></span>'
      +'</div>';
  }
  $("pRows").innerHTML=h;
  planFreqs();
}
/* ★ 通道 -> 相对中心的 25k 步数：MMDVM-Multi 的 PFB 子带映射
   （本机 SampleRate=150000 ⇒ 6 个 bin，Receiver/Transmitter 里
     m_fillReal = min(6/2-1, 4) = 2，其余通道从高 bin 逆序取）
     ⇒ ch1=bin0(0) ch2=bin1(+25k) ch3=bin5(-25k) ch4=bin4(-50k) ch5=bin3(+75k)
   旧代码用 (i-1)*25k 线性推算，与硬件不符。 */
var CH_GAP=25000;
/* 按采样率推导网格（与 MMDVM-Multi 源码代数一致）：
   n_pfb = SampleRate/25000，m_fill = min(max(n_pfb/2-1,1),4)
   p=ch-1；bin = p<m_fill ? p : (n_pfb-1)-(p-m_fill)；
   频率步数 = bin < n_pfb/2 ? bin : bin-n_pfb
   150k（PFB=6）-> 0/+1/-1/-2/+3；250k（PFB=10）-> 0/+1/+2/+3/-1 */
function chGridStep(i, sr){
  sr = parseInt(sr, 10) || parseInt((typeof PLN === 'object' && PLN && PLN.sample_rate) || 0, 10) || 250000;
  var n = Math.max(1, Math.round(sr / CH_GAP));
  var mf = Math.max(1, Math.min(Math.floor(n/2)-1, 4));   /* 与 MMDVM-Multi 源码：m_fillReal = PFB/2 */
  var p = i - 1;
  var b = (p < mf) ? p : (n - 1) - (p - mf);
  return (b * 2 <= n) ? b : b - n;   /* bin == n/2 是 +fs/2（正频边缘）*/
}
function chanFreq(i,c){ return c + chGridStep(i)*CH_GAP; }
function fmtMHz(hz){ return (hz/1e6).toFixed(4)+" MHz"; }
function planFreqs(){
  var e1=$("pCrx"), e2=$("pCtx");
  var crx=parseInt(e1&&e1.value,10)||0, ctx=parseInt(e2&&e2.value,10)||0;
  for(var i=1;i<=MC_DYN_CH();i++){   /* MC_DYN_CH 动态通道数 */
    var r=$("pr"+i), t=$("pt"+i);
    if(r && crx){ r.value = String(chanFreq(i,crx)); }
    if(t && ctx){ t.value = String(chanFreq(i,ctx)); }
    if($("fr"+i)){ $("fr"+i).textContent = crx?fmtMHz(chanFreq(i,crx)):"--"; }
    if($("ft"+i)){ $("ft"+i).textContent = ctx?fmtMHz(chanFreq(i,ctx)):"--"; }
  }
}
/* 用户改某一路频率 -> 以该路为准反推中心频率，然后整体刷新（其它通道按网格跟着走） */
function planSpans(){
  for(var i=1;i<=MC_DYN_CH();i++){
    var r=$("pr"+i), t=$("pt"+i);
    var rv=parseInt(r&&r.value,10)||0, tv=parseInt(t&&t.value,10)||0;
    if($("fr"+i)) $("fr"+i).textContent=rv>0?fmtMHz(rv):'--';
    if($("ft"+i)) $("ft"+i).textContent=tv>0?fmtMHz(tv):'--';
  }
}
function onChFreq(i){ planSpans(); }
function planCollect(){
  var lst=[];
  for(var i=1;i<=MC_DYN_CH();i++){   /* MC_DYN_CH 动态通道数 */
    if(!$("pm"+i)) continue;                 // 行不存在则跳过
    /* 绝对频率：保留本页每个通道输入框中的 RX/TX，后端负责校验并自动计算中心/bin。 */
    var rr=parseInt(($("pr"+i)||{}).value,10)||0, tt=parseInt(($("pt"+i)||{}).value,10)||0;
    lst.push({n:i, mode:$("pm"+i).value, rx:rr, tx:tt});
  }
  if(!lst.length) return null;
  var e1=$("pCrx"), e2=$("pCtx");
  var crx=parseInt(e1&&e1.value,10)||PLN.center_rx||434500000;
  var ctx=parseInt(e2&&e2.value,10)||PLN.center_tx||439500000;
  return {channels:MC_DYN_CH(), callsign:pv("pCall"), dmr_id:pv("pId"),
          /* 中心频率 = 通道 1；其余通道按 25 kHz 网格自动推算 */
          center_rx:crx, center_tx:ctx,
          spacing:25000, sample_rate:(PLN.sample_rate||150000), list:lst};
}
function planShow(msg,ok){
  var o=$("pOut"); o.style.display="block"; o.textContent=msg;
  o.style.color=(ok===false)?"#fca5a5":"#9fd8b4";
}
function planLoad(){
  fetch("/api/mc/plan").then(function(r){return r.json();}).then(function(d){
    if(!d||d.ok===false||!d.list||!d.list.length){
      planShow("读取失败: "+((d&&d.error)||"无数据"),false); return;
    }
    PLN=d;
    pv("pCall",d.callsign); pv("pId",d.dmr_id);
    if($("pCrx")){ $("pCrx").value=d.center_rx||434500000; }
    if($("pCtx")){ $("pCtx").value=d.center_tx||439500000; }
    if(d.sample_rate){ PLN.sample_rate=d.sample_rate; }
    planRender(d.channels);
  }).catch(function(e){ planShow("网络错误: "+e,false); });
}
function planPush(mode){
  var p=planCollect();
  if(!p){ planShow("没有可用的通道行", false); return; }
  if(mode==="apply"&&!confirm("将写入全部配置文件并重启 MMDVM-Multi 与全部通道。\n\n请遵守所在国家或地区关于无线电发射设备、频率使用和操作资格的法律法规。因违规使用造成的后果由操作者承担，与本软件及作者无关。\n\n继续？")){ return; }
  planShow(mode==="apply"?"正在应用规划（约 30-60 秒，含服务重启）...":"正在保存规划...");
  fetch("/api/mc/plan-"+mode,{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(p)})
  .then(function(r){return r.json();}).then(function(d){
    var ok=(d&&d.ok)!==false;
    planShow((ok?"[OK] ":"[失败] ")+((d&&(d.message||d.out||d.error))||JSON.stringify(d)),ok);
    if(ok){ setTimeout(refresh,1500); setTimeout(planLoad,2000); try{ if(typeof mcInvalidateChannels === 'function') mcInvalidateChannels(); }catch(e){} }
  }).catch(function(e){ planShow("网络错误: "+e,false); });
}
function planApply(){ planPush("apply"); }
function planSave(){ planPush("save"); }
planLoad();

refresh();
setInterval(refresh,5000);
</script>
</body>
</html>'''


@app.route("/rfmode")
def rfmode_page_view():
    return render_template_string(RFMODE_STANDALONE)


@app.route("/multi")
def mc_page():
    return render_template_string(MC_HTML)


@app.route("/api/mc/channels")
def mc_channels():
    light = "light" if request.args.get("light") == "1" else "full"
    return _mc_reply("channels", light, timeout=40)


@app.route("/api/mc/switch", methods=["POST"])
def mc_switch():
    d = request.get_json(silent=True) or {}
    ch, mode = str(d.get("ch", "")), str(d.get("mode", ""))
    if not ch or not mode:
        return jsonify({"ok": False, "error": "缺少 ch / mode"}), 400
    return _mc_reply("switch", ch, mode, timeout=160)


@app.route("/api/mc/restart", methods=["POST"])
def mc_restart():
    d = request.get_json(silent=True) or {}
    ch = str(d.get("ch", ""))
    if not ch:
        return jsonify({"ok": False, "error": "缺少 ch"}), 400
    return _mc_reply("restart", ch, timeout=120)


@app.route("/api/mc/multi-restart", methods=["POST"])
def mc_multi_restart():
    return _mc_reply("multi-restart", timeout=200)


@app.route("/api/mc/logs")
def mc_logs():
    return _mc_reply("logs",
                     request.args.get("ch", "0"),
                     request.args.get("svc", "mmdvmhost"),
                     request.args.get("lines", "200"), timeout=40)


@app.route("/api/mc/plan")
def mc_plan():
    return _mc_reply("plan", timeout=60)


@app.route("/api/mc/plan-apply", methods=["POST"])
def mc_plan_apply():
    body = json.dumps(request.get_json(silent=True) or {}, ensure_ascii=False)
    return _mc_reply("plan-apply", body, timeout=300)


@app.route("/api/mc/plan-save", methods=["POST"])
def mc_plan_save():
    body = json.dumps(request.get_json(silent=True) or {}, ensure_ascii=False)
    return _mc_reply("plan-save", body, timeout=90)


# ==================== 多信道扩展结束 ====================

# ==================== FM 联网配置页 ====================
_FMO_HELPER = "/usr/local/bin/web-fmo.sh"


def _fmo_call(*args, timeout=90):
    try:
        r = subprocess.run(["sudo", "-n", _FMO_HELPER] + [str(a) for a in args],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _fmo_reply(*args, timeout=90):
    rc, out, err = _fmo_call(*args, timeout=timeout)
    _bad = False
    try:
        d = json.loads(out) if out else {}
        if not isinstance(d, dict):
            d = {"out": out}; _bad = True
    except Exception:
        d = {"out": out}; _bad = True
    if "ok" not in d:
        # [FIX 1.0.86] never fake ok=true on parse-fail/empty output (front-end crash)
        d["ok"] = bool(rc == 0 and not _bad)
        if not d["ok"]:
            d.setdefault("error", (err or out or "FMO helper returned nothing").strip()[:500])
    if err:
        d["stderr"] = err
    return jsonify(d), (200 if d.get("ok") else 500)


@app.route("/fmo")
def fmo_page():
    return render_template_string(FMO_HTML)


@app.route("/api/fmo/status")
def fmo_status():
    return _fmo_reply("status", timeout=40)




@app.route("/api/fmo/ping", methods=["POST"])
def fmo_ping():
    d = request.get_json(silent=True) or {}
    return _fmo_reply("ping", d.get("host", ""), d.get("port", 8081), timeout=60)


@app.route("/api/fmo/register", methods=["POST"])
def fmo_register():
    d = request.get_json(silent=True) or {}
    return _fmo_reply("register", d.get("host", ""), d.get("port", 8081),
                      d.get("callsign", ""), d.get("uid", 0), timeout=150)


@app.route("/api/fmo/unregister", methods=["POST"])
def fmo_unregister():
    return _fmo_reply("unregister", timeout=60)


@app.route("/api/fmo/service", methods=["POST"])
def fmo_service():
    d = request.get_json(silent=True) or {}
    return _fmo_reply("service", d.get("action", "restart"), d.get("what", "bridge"), timeout=60)


@app.route("/api/fmo/ctcss")
def fmo_ctcss():
    return _fmo_reply("ctcss")


@app.route("/api/fmo/tailtone")
def fmo_tailtone():
    return _fmo_reply("tailtone", timeout=30)


@app.route("/api/fmo/tailtone/save", methods=["POST"])
def fmo_tailtone_save():
    body = json.dumps(request.get_json(silent=True) or {}, ensure_ascii=False)
    return _fmo_reply("tailtone-set", body, timeout=120)


@app.route("/api/fmo/apply-ctcss", methods=["POST"])
def fmo_apply_ctcss():
    body = json.dumps(request.get_json(silent=True) or {}, ensure_ascii=False)
    return _fmo_reply("apply-ctcss", body, timeout=160)


@app.route("/api/fmo/restart", methods=["POST"])
def fmo_restart():
    d = request.get_json(silent=True) or {}
    return _fmo_reply("restart", d.get("what", "all"), timeout=160)







@app.route("/api/fmo/tone-test", methods=["POST"])
def fmo_tone_test():
    return _fmo_reply("tone-test")




# ==================== FM 联网配置页结束 ====================



    app.run(host='127.0.0.1', port=18080)



