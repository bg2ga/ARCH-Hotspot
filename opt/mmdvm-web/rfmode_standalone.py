# -*- coding: utf-8 -*-
"""把 rfmode_page 的片段组装成【独立页面】（像 /configuration 那样，自带样式与脚本）
   —— 独立页面的脚本即使出错，也完全不影响主控制台 ✓"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "rfmode_frag", "/opt/mmdvm-web/rfmode_page.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_CSS = ""
_HTML = _mod.RFMODE_HTML
# 片段里第一段是 <style>…</style>，抽出来单独放 <head>
import re as _re
_m = _re.search(r"<style>(.*?)</style>", _HTML, _re.S)
if _m:
    _CSS = _m.group(1)
    _HTML = _HTML[:_m.start()] + _HTML[_m.end():]

RFMODE_STANDALONE = (
    "<!DOCTYPE html>\n"
    "<html lang=\"zh-CN\">\n<head>\n"
    "<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
    "<title>射频模式配置 · MMDVM-Multi</title>\n"
    "<style>\n"
    "*{box-sizing:border-box;margin:0;padding:0}\n"
    "body{background:#0b1220;color:#dfe8f5;font:14.5px/1.6 \"Segoe UI\",\"Microsoft YaHei\",sans-serif;padding:14px}\n"
    "body.embed{padding:6px 10px 10px}\n"
    "body.embed .rfm-hd h1{display:none}\n"
    ":root{--mono:Consolas,\"Cascadia Mono\",\"DejaVu Sans Mono\",monospace}\n"
    + _CSS +
    "\n</style>\n</head>\n<body>\n"
    + _HTML +
    "\n<script>\n"
    # 独立页面里没有主控制台的 toast()，这里给一个最简版（用主主题的居中弹窗样式）
    "function toast(msg, kind){\n"
    "  var t = document.getElementById('toast');\n"
    "  if(!t){ t = document.createElement('div'); t.id='toast'; document.body.appendChild(t); }\n"
    "  t.textContent = String(msg == null ? '' : msg);\n"
    "  t.style.cssText = 'position:fixed;left:50%;top:50%;transform:translate(-50%,-50%) scale(.96);"
    "z-index:99999;background:rgba(13,21,32,.97);border:1px solid #2b3f57;color:#eaf2ff;"
    "padding:20px 34px;border-radius:14px;text-align:center;max-width:min(560px,86vw);"
    "font-size:16px;font-weight:600;line-height:1.6;display:block;opacity:0;pointer-events:none;"
    "transition:opacity .3s ease,transform .3s ease';\n"
    "  requestAnimationFrame(function(){ t.style.opacity='1'; t.style.transform='translate(-50%,-50%) scale(1)'; });\n"
    "  clearTimeout(window.__rtt1); clearTimeout(window.__rtt2);\n"
    "  window.__rtt1 = setTimeout(function(){ t.style.opacity='0'; t.style.transform='translate(-50%,-50%) scale(.96)'; }, 2000);\n"
    "  window.__rtt2 = setTimeout(function(){ t.style.display='none'; }, 2400);\n"
    "}\n"
    + _mod.RFMODE_JS +
    "\n// 独立页面启动\ndocument.addEventListener('DOMContentLoaded', function(){ load(); });\n"
    "if(document.readyState !== 'loading'){ load(); }\n"
    "</script>\n</body>\n</html>\n"
)

if __name__ == "__main__":
    open("/tmp/_rfmode_chk.html", "w", encoding="utf-8").write(RFMODE_STANDALONE)
    print("CSS %d 字节 / HTML片段 %d / JS %d / 整页 %d 字节" % (
        len(_CSS), len(_HTML), len(_mod.RFMODE_JS), len(RFMODE_STANDALONE)))
    print("关键元素: " + ", ".join(
        "%s=%d" % (k, RFMODE_STANDALONE.count(k))
        for k in ("rfmLoad", "rfmSwitch", "rfmLoadFreq", "rfmSaveFreq", "rfmLoadLog",
                  "<style>", "</style>", "<script>", "</script>", "<body>", "</body>")))
