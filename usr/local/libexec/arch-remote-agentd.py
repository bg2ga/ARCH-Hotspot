#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ARCH 远程调测守护：仅监听 127.0.0.1:42172，需口令才给 root shell。"""
import hmac, os, pty, select, socket, subprocess, threading, time
PORT = 42172
SECRET_FILE = "/etc/mmdvm/remote-debug.secret"
FLAG = "/etc/mmdvm/remote-debug.enable"
LOG = "/var/log/arch-remote-debug.log"


def load_secret():
    try:
        with open(SECRET_FILE, encoding="ascii") as f:
            value = f.read().strip()
        return value if len(value) >= 32 else ""
    except Exception:
        return ""

def log(m):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%F %T"), m))
    except Exception:
        pass

def enabled():
    try:
        return open(FLAG, encoding="ascii").read().strip() != "0"
    except Exception:
        return True

def serve(conn, addr):
    try:
        conn.settimeout(10)
        buf = b""
        while b"\n" not in buf and len(buf) < 128:
            c = conn.recv(1)
            if not c:
                conn.close(); return
            buf += c
        expected = load_secret()
        supplied = buf.decode("utf-8", "ignore").strip()
        if not expected or not hmac.compare_digest(supplied, expected):
            conn.sendall(b"DENIED\n"); conn.close(); log("auth fail %s" % (addr,)); return
        conn.settimeout(None)
        conn.sendall(b"ARCH-ROOT-SHELL READY\n")
        log("remote shell open %s" % (addr,))
        # [FIX 1.0.119] 不要在线程里 pty.fork（子进程会继承 socket，导致会话立刻 EOF）。
        # 改为 openpty + subprocess，父进程关闭 slave，子进程不持有 socket。
        master, slave = pty.openpty()
        proc = subprocess.Popen(["/bin/bash", "-i"],
                                stdin=slave, stdout=slave, stderr=slave,
                                start_new_session=True, close_fds=True,
                                env=dict(os.environ, TERM="xterm", PS1=""))
        os.close(slave)
        fd = master
        while True:
            r, _, _ = select.select([conn, fd], [], [], 300)
            if not r:
                break
            for x in r:
                try:
                    if x is conn:
                        d = conn.recv(4096)
                        if not d: raise OSError("closed")
                        os.write(fd, d)
                    else:
                        d = os.read(fd, 4096)
                        if not d: raise OSError("eof")
                        conn.sendall(d)
                except Exception:
                    try: proc.kill()
                    except Exception: pass
                    try: os.close(fd)
                    except Exception: pass
                    conn.close(); log("remote shell closed %s" % (addr,)); return
    except Exception as e:
        log("session err %r" % e)
        try: conn.close()
        except Exception: pass

def main():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", PORT)); s.listen(4)
    log("agentd on 127.0.0.1:%d" % PORT)
    while True:
        try:
            c, a = s.accept()
            if not enabled():
                c.sendall(b"DISABLED\n"); c.close(); continue
            threading.Thread(target=serve, args=(c, a), daemon=True).start()
        except Exception as e:
            log("accept err %r" % e); time.sleep(1)

if __name__ == "__main__":
    main()
