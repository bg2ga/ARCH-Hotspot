# -*- coding: utf-8 -*-
"""硬件基础配置（原 Multi 看板）—— 只留「监控/诊断」用途
   呼号（最有用的那个）与通道数量/频率规划已并入「射频模式配置」页 ✓
   DMR ID 已去掉（它只对 DMR 有效，归 DMR 模式页）"""

MULTI2_SECTION = r"""
    <section class="page proto-page" id="page-multi" style="display:none">

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(56,189,248,.12);color:#38bdf8">&#129520;</div>
            <div>
              <div class="card-title">硬件与引擎状态（MMDVM-Multi &middot; SX1255 共享 I/Q 前端）</div>
              <div class="card-sub">只读监控：引擎状态、采样率、中心频率、增益、各通道与服务运行情况</div>
            </div>
          </div>
          <span class="card-badge">只读监控</span>
        </div>

        <div class="fld full"><div class="hint" style="border:1px dashed var(--border-lit);border-radius:9px;padding:10px 12px;line-height:1.7">
          <b style="color:#fbbf24">本页只做监控。</b>
          <b>呼号</b>、<b>通道数量</b>、<b>收发频率</b>与<b>各通道协议</b>都已集中到
          「<b>&#128251; 射频模式配置</b>」页（原来的 DMR ID 属于 DMR 模式，已移出本页）。
          <div style="margin-top:9px">
            <button type="button" class="btn btn-green btn-sm" onclick="switchPage('rfmode')">&#8594; 前往「射频模式配置」</button>
          </div>
        </div></div>

        <div class="form-grid" id="mcEngBox" style="margin-top:8px">
          <div class="hint">正在读取引擎状态&hellip;</div>
        </div>

        <div class="mc-foot" style="margin:12px 0 0">
          <button class="btn btn-ghost btn-sm" onclick="mcRefreshAll()">&#8635; 立即刷新</button>
          <button class="btn btn-ghost btn-sm" onclick="mcRestartEngine()">&#8634; 重启 Multi 引擎</button>
          <span class="hint" id="mcMsg" style="margin-left:10px"></span>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:var(--green-dim);color:var(--green2)">&#128225;</div>
            <div>
              <div class="card-title">各通道运行状态</div>
              <div class="card-sub">IQ 基带 / MMDVM-Host 是否在跑，以及最近一次通联</div>
            </div>
          </div>
          <span class="card-badge" id="mcChBadge">通道 --</span>
        </div>
        <div id="mcChBox"><div class="sp-empty">正在读取通道&hellip;</div></div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:var(--amber-dim);color:var(--amber2)">&#128202;</div>
            <div>
              <div class="card-title">服务状态</div>
              <div class="card-sub">MMDVM-Multi / 各通道 / 网关 / Web 服务</div>
            </div>
          </div>
          <span class="card-badge" id="mcSvcBadge">--</span>
        </div>
        <div id="mcSvcBox" class="form-grid"><div class="hint">正在读取服务&hellip;</div></div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:rgba(148,163,184,.12);color:#94a3b8">&#128220;</div>
            <div>
              <div class="card-title">日志查看</div>
              <div class="card-sub">查看 MMDVM-Multi / 各通道 / 各网关的运行日志</div>
            </div>
          </div>
        </div>
        <div class="form-grid">
          <div class="fld"><label>通道</label><select id="mcLogCh"></select></div>
          <div class="fld"><label>服务</label>
            <select id="mcLogSvc">
              <option value="mmdvmhost">MMDVM-Host</option>
              <option value="mmdvm-iq">MMDVM-IQ</option>
              <option value="mmdvm-multi">MMDVM-Multi</option>
              <option value="dmrgateway">DMRGateway</option>
              <option value="ircddbgatewayd">ircDDBGateway (D-STAR)</option>
              <option value="ysfgateway">YSFGateway</option>
              <option value="p25gateway">P25Gateway</option>
              <option value="nxdngateway">NXDNGateway</option>
            </select></div>
          <div class="fld"><label>行数</label>
            <select id="mcLogLines">
              <option value="100">100 行</option>
              <option value="200" selected>200 行</option>
              <option value="500">500 行</option>
            </select></div>
        </div>
        <div class="mc-foot" style="margin:10px 0 0">
          <button class="btn btn-green btn-sm" onclick="mcLoadLog()">&#128203; 加载日志</button>
        </div>
        <pre id="mcLogBox" style="margin-top:10px;max-height:380px;overflow:auto">点击「加载日志」查看。</pre>
      </div>

    </section>
"""
