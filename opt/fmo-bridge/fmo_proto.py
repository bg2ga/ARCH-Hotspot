# -*- coding: utf-8 -*-
"""FMO 4.0 协议 / 密码学核心。

与 BG5ESN 的 SAS（fmo-server-authrozier-service）1:1 对齐：
  · 签名输入 = 固定顺序语义值数组 → 确定性 CBOR 编码（不是 JSON 文本）
  · 算法固定 Ed25519
  · binary 字段在 JSON/文本里用 base64url（无 padding）
  · 域分离前缀 ["FMO", 4, "<messageType>", ...]

依赖：cbor2（apt: python3-cbor2）、PyNaCl（apt: python3-nacl）
"""
import base64
import hashlib
import json
import time

import cbor2
from nacl.signing import SigningKey, VerifyKey

PROTOCOL = "FMO"
VERSION = 4


# ---------------------------------------------------------------- 编码工具
def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64u_decode(text) -> bytes:
    if isinstance(text, bytes):
        text = text.decode("ascii")
    text = text.strip()
    text += "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text)


def cbor_det(obj) -> bytes:
    """确定性（canonical）CBOR —— 对齐 .NET CborWriter 的 preferred serialization。"""
    return cbor2.dumps(obj, canonical=True)


def sha256(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


# ---------------------------------------------------------------- 证书 TBS
def root_ca_tbs(j: dict) -> bytes:
    ext = j.get("extensions", {})
    return cbor_det([
        PROTOCOL, VERSION, "rootCA",
        int(j["sn"]),
        j["issuer"]["name"], j.get("issuer", {}).get("email", ""),
        j["subject"]["name"], b64u_decode(j["subject"]["publicKey"]),
        bool(ext.get("isCA", True)), int(ext.get("pathLen", 1)),
        ext.get("crl", ""), ext.get("license", ""), str(ext.get("keyId", "")),
        int(j["iat"]), int(j["exp"]),
    ])


def intermediate_ca_tbs(j: dict) -> bytes:
    ext = j.get("extensions", {})
    ur = ext.get("uidRange", {})
    countries = sorted(str(c).upper() for c in ext.get("issuingCountries", []))
    return cbor_det([
        PROTOCOL, VERSION, "intermediateCA",
        int(j["sn"]),
        int(j["issuer"]["sn"]), j["issuer"]["name"], b64u_decode(j["issuer"]["publicKey"]),
        j["subject"]["name"], j.get("subject", {}).get("email", ""),
        b64u_decode(j["subject"]["publicKey"]),
        bool(ext.get("isCA", True)), int(ext.get("pathLen", 0)), str(ext.get("keyId", "")),
        ext.get("crl", ""), ext.get("license", ""),
        int(ur.get("start", 0)), int(ur.get("end", 0)),
        countries,
        int(j["iat"]), int(j["exp"]),
    ])


def user_cert_tbs(j: dict) -> bytes:
    sub = j["subject"]
    return cbor_det([
        PROTOCOL, VERSION, "userCert",
        int(j["issuerSn"]),
        str(sub["callsign"]).upper(),
        int(sub["uid"]),
        b64u_decode(sub["publicKey"]),
        int(j["iat"]), int(j["exp"]),
    ])


# ---------------------------------------------------------------- 证书对象
class Cert:
    def __init__(self, j: dict, kind: str):
        self.raw = j
        self.kind = kind
        if kind == "rootCA":
            self.tbs = root_ca_tbs(j)
            self.subject_pub = b64u_decode(j["subject"]["publicKey"])
        elif kind == "intermediateCA":
            self.tbs = intermediate_ca_tbs(j)
            self.subject_pub = b64u_decode(j["subject"]["publicKey"])
            self.issuer_pub = b64u_decode(j["issuer"]["publicKey"])
            self.sn = int(j["sn"])
        elif kind == "userCert":
            self.tbs = user_cert_tbs(j)
            self.subject_pub = b64u_decode(j["subject"]["publicKey"])
            self.callsign = str(j["subject"]["callsign"]).upper()
            self.uid = int(j["subject"]["uid"])
            self.issuer_sn = int(j["issuerSn"])
            self.exp = int(j["exp"])
        else:
            raise ValueError("unknown cert kind: %s" % kind)
        self.signature = b64u_decode(j["signature"])

    def fingerprint(self) -> bytes:
        """SHA-256(TBS 的确定性 CBOR) —— 即 certFingerprint / serverFingerprint。"""
        return sha256(self.tbs)

    def verify_by(self, issuer_public_key: bytes) -> bool:
        try:
            VerifyKey(issuer_public_key).verify(self.tbs, self.signature)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------- MQTT 登录
TBS_TYPE_HTTP = "serverAuthorizerReqHttp"


def server_authorizer_tbs(server_uid, target_callsign, target_uid, role,
                          target_url, target_port, server_fp_bytes,
                          timestamp, user_fp_bytes) -> bytes:
    """SAS HTTP 认证的 12 元素签名数组（与 HttpProofVerifier.BuildTbs 一致）。"""
    return cbor_det([
        PROTOCOL, VERSION, TBS_TYPE_HTTP,
        int(server_uid),
        str(target_callsign).upper(),
        int(target_uid),
        str(role),
        str(target_url),
        int(target_port),
        server_fp_bytes,
        int(timestamp),
        user_fp_bytes,
    ])


def build_mqtt_login(user_cert_json: dict,
                     intermediate_cert_json: dict,
                     user_seed: bytes,
                     server_callsign: str,
                     server_uid: int,
                     mqtt_host: str,
                     mqtt_port: int,
                     server_fingerprint_b64u: str,
                     role: str = "user",
                     timestamp: int = None) -> dict:
    """构造 MQTT CONNECT 的 username/password（FMO 4.0 SAS HTTP 认证器模式）。"""
    uc = Cert(user_cert_json, "userCert")
    ts = int(time.time()) if timestamp is None else int(timestamp)
    server_fp = b64u_decode(server_fingerprint_b64u)

    tbs = server_authorizer_tbs(server_uid, server_callsign, server_uid, role,
                                mqtt_host, int(mqtt_port), server_fp, ts, uc.fingerprint())
    sig = SigningKey(bytes(user_seed)).sign(tbs).signature          # 64 字节

    payload = {
        "certPackage": {
            "intermediateCert": intermediate_cert_json,
            "userCert": user_cert_json,
        },
        "targetCallsign": server_callsign,
        "targetUID": int(server_uid),
        "role": role,
        "targetUrl": mqtt_host,
        "targetPort": int(mqtt_port),
        "serverFingerprint": server_fingerprint_b64u,
        "timestamp": ts,
        "proof": {"signature": b64u_encode(sig)},
    }
    return {
        "username": uc.callsign,
        "password": b64u_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        "_tbs_hex": tbs.hex(),
    }
