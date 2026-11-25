# -*- coding: utf-8 -*-
"""FMO 客户端自动注册（C 方案：服务器自动签发证书）

用户在 FM 配置里只需填「服务器地址 / 端口 / 呼号」：
  1. 本机生成 Ed25519 密钥对 —— **私钥永不离开本机**
  2. GET  <注册地址>/fmo/ping     → 取得服务器身份(uid/callsign/证书指纹/MQTT端口)
  3. POST <注册地址>/fmo/register → 提交 呼号+公钥，服务器用其中级 CA 现场签发
  4. userCert / intermediateCert / 私钥 落盘，之后照常登录

注册地址约定：http://<服务器地址>:8081
"""
import json
import os
import sys
import urllib.request

from nacl.signing import SigningKey

import fmo_proto as P

REG_PORT = 8081
TIMEOUT = 15


def register_base(host, port=REG_PORT):
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/")
    return "http://%s:%d" % (host, int(port))


def _get_json(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(url, body, timeout=TIMEOUT):
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def probe_server(register_url, timeout=TIMEOUT):
    """只查询服务器身份，不签发证书。"""
    return _get_json(register_url.rstrip("/") + "/fmo/ping", timeout)


def _stderr_log(msg):
    try:
        sys.stderr.write(str(msg) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def ensure_cert(register_url, callsign, uid, cert_path, key_path, inter_path,
                token=None, timeout=TIMEOUT, force=False, log=_stderr_log):
    """确保证书齐备；缺失则自动注册。返回 (server_info|None, userCert, intermediateCert)。"""
    have = all(os.path.exists(p) for p in (cert_path, key_path, inter_path))
    if have and not force:
        with open(cert_path, encoding="utf-8") as f:
            cert = json.load(f)
        with open(inter_path, encoding="utf-8") as f:
            inter = json.load(f)
        return None, cert, inter

    base = register_url.rstrip("/")
    info = probe_server(base, timeout)
    if not info.get("ok"):
        raise RuntimeError("注册服务无响应: %s" % info)
    server = info.get("server") or {}
    if not server:
        raise RuntimeError("服务器未下发身份信息（CA 未读到 SAS 配置）")

    d = os.path.dirname(os.path.abspath(cert_path))
    if d:
        os.makedirs(d, exist_ok=True)

    sk = SigningKey.generate()
    pub = P.b64u_encode(sk.verify_key.encode())

    body = {"callsign": str(callsign).upper(), "uid": int(uid), "pubkey": pub}
    if token:
        body["token"] = token
    res = _post_json(base + "/fmo/register", body, timeout)
    if not res.get("ok"):
        raise RuntimeError("注册被拒绝: %s" % res.get("error"))

    user = res["userCert"]
    sub = user.get("subject", {})
    if str(sub.get("callsign", "")).upper() != str(callsign).upper():
        raise RuntimeError("证书呼号不匹配: %s" % sub.get("callsign"))
    # uid 传 0 表示由服务器分配唯一值（FAS 会封禁同一 uid 多处登录）
    if int(uid) > 0 and int(sub.get("uid", -1)) != int(uid):
        raise RuntimeError("证书 uid 不匹配: %s" % sub.get("uid"))
    if sub.get("publicKey") != pub:
        raise RuntimeError("证书公钥与本地私钥不匹配（服务器未采用我们提交的公钥）")

    with open(cert_path, "w", encoding="utf-8") as f:
        json.dump(user, f, ensure_ascii=False, indent=2)
    with open(inter_path, "w", encoding="utf-8") as f:
        json.dump(res["intermediateCert"], f, ensure_ascii=False, indent=2)
    with open(key_path, "w", encoding="ascii") as f:
        f.write(P.b64u_encode(sk.encode()) + "\n")
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass

    log("自动注册成功：%s uid=%d fingerprint=%s"
        % (user["subject"]["callsign"], user["subject"]["uid"],
           res.get("fingerprint", "?")))
    return server, user, res["intermediateCert"]

