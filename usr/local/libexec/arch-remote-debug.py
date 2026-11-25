#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ARCH 远程调测 Agent v2（root 权限，等价 SSH 运维能力）
   通道：只有 HTTP 出站（92:8081），不需要公网入站/开放端口/VPN。
   支持 job.kind:
     cmd     执行 root 命令并回传输出
     putfile 服务器 -> 设备：把 job.data(base64) 写入 job.path
     getfile 设备 -> 服务器：读取 job.path 回传
     reboot / restart / engine 等快捷动作
   开关：/etc/mmdvm/remote-debug.enable（1=开 0=关），默认开
"""
import base64, hashlib, json, os, platform, re, shutil, subprocess, sys, time, urllib.parse, urllib.request

BASE = os.environ.get("ARCH_AGENT_BASE", "http://fmo.hljrsa.cn:8081")
FLAG = "/etc/mmdvm/remote-debug.enable"
LOG = "/var/log/arch-remote-debug.log"
MAXOUT = 200000


def log(m):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%F %T"), m))
    except Exception:
        pass


def callsign():
    for p in ("/etc/MMDVM-Host.ini", "/etc/DMRGateway.ini"):
        try:
            for ln in open(p, encoding="utf-8", errors="replace"):
                if ln.startswith("Callsign="):
                    v = ln.split("=", 1)[1].strip().upper()
                    if v and v != "NOCALL":
                        return v
        except Exception:
            pass
    return "NOCALL"


def md5(p):
    try:
        return hashlib.md5(open(p, "rb").read()).hexdigest()[:12]
    except Exception:
        return ""


def device_id():
    try:
        v = open("/etc/mmdvm/device-id", encoding="ascii").read().strip().lower()
        v = re.sub(r"[^a-z0-9_-]", "", v)[:64]
        if v:
            return v
    except Exception:
        pass
    try:
        r = subprocess.run(["/usr/local/bin/arch-device-id"], capture_output=True, text=True, timeout=5)
        v = re.sub(r"[^a-z0-9_-]", "", (r.stdout or "").strip().lower())[:64]
        if v:
            return v
    except Exception:
        pass
    raw = platform.node().lower()
    v = re.sub(r"[^a-z0-9]", "", raw)[-12:] or "unknown"
    return "arch-" + v


def tunnel_ports():
    try:
        parts = open("/etc/mmdvm/remote-tunnel.port", encoding="ascii").read().split()
        port = parts[2] if len(parts) >= 3 and parts[2].isdigit() else ""
        web_port = parts[3] if len(parts) >= 4 and parts[3].isdigit() else ""
        return port, web_port
    except Exception:
        pass
    return "", ""


def lan_ip():
    # Return the IPv4 address of the interface carrying the default route.
    try:
        r = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], capture_output=True, text=True, timeout=3)
        m = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", r.stdout or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        r = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3)
        for ip in (r.stdout or "").split():
            if ip.startswith(("10.", "172.", "192.168.")):
                return ip
    except Exception:
        pass
    return ""



def model():
    for p in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            return open(p, "rb").read().replace(b"\x00", b"").decode("utf-8", "ignore").strip()[:40]
        except Exception:
            pass
    return platform.machine()[:40]


def ver():
    try:
        for ln in open("/etc/arch-hotspot-release", encoding="utf-8", errors="replace"):
            if ln.startswith("VERSION="):
                return ln.split("=", 1)[1].strip()
    except Exception:
        pass
    return "?"


def http(url, data=None):
    req = urllib.request.Request(url, data=(json.dumps(data).encode() if data is not None else None),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def run_cmd(cmd):
    try:
        r = subprocess.run(["/bin/bash", "-c", cmd], capture_output=True, text=True, timeout=180)
        return (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "") + ("\n[rc=%d]" % r.returncode)
    except Exception as e:
        return "执行异常: %r" % e


def do_job(j):
    k = (j or {}).get("kind") or "cmd"
    if k == "cmd":
        return run_cmd(str(j.get("cmd") or ""))
    if k == "putfile":
        path = str(j.get("path") or "")
        raw = base64.b64decode(str(j.get("data") or ""))
        os.makedirs(os.path.dirname(path) or "/", exist_ok=True)
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak-agent-" + time.strftime("%Y%m%d-%H%M%S"))
        open(path, "wb").write(raw)
        try:
            os.chmod(path, int(str(j.get("mode") or "644"), 8))
        except Exception:
            pass
        return "putfile OK %s %d 字节 md5=%s" % (path, len(raw), hashlib.md5(raw).hexdigest())
    if k == "getfile":
        path = str(j.get("path") or "")
        raw = open(path, "rb").read()
        return base64.b64encode(raw).decode()
    if k == "restart":
        s = str(j.get("svc") or "")
        if not s:
            return "need svc"
        r = subprocess.run(["systemctl", "restart", s], capture_output=True, text=True)
        time.sleep(3)
        a = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True).stdout.strip()
        return "restart %s rc=%d active=%s\n%s" % (s, r.returncode, a, r.stderr)
    if k == "reboot":
        subprocess.Popen(["/bin/systemctl", "reboot"])
        return "reboot 已下发"
    return "未知 kind: %s" % k


def main():
    if os.path.exists(FLAG):
        try:
            if open(FLAG, encoding="ascii").read().strip() == "0":
                return 0
        except Exception:
            pass
    cs = callsign()
    did = device_id()
    port, web_port = tunnel_ports()
    q = "?" + urllib.parse.urlencode({"callsign": cs, "id": did, "port": port, "web_port": web_port, "lan_ip": lan_ip(), "ver": ver(), "engine": md5("/usr/local/bin/MMDVM-Multi"), "model": model()})
    try:
        d = http(BASE + "/fmo/agent/poll" + q)
    except Exception as e:
        log("poll 失败 id=%s %r" % (did, e))
        return 0
    job = (d or {}).get("job") or {}
    if not job:
        return 0
    log("任务 id=%s kind=%s" % (did, job.get("kind")))
    out = do_job(job)
    try:
        http(BASE + "/fmo/agent/output", {"id": did, "callsign": cs, "out": str(out)[:MAXOUT]})
        log("已回传 id=%s %d 字节" % (did, len(str(out))))
    except Exception as e:
        log("回传失败 id=%s %r" % (did, e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

def _arch_power_hex():
    """[1.0.144] 上报供电/降频状态（vgencmd get_throttled）"""
    try:
        import subprocess as _sp
        return _sp.check_output(['vcgencmd', 'get_throttled'], text=True, timeout=5).strip().split('=')[-1]
    except Exception:
        return ''
