# -*- coding: utf-8 -*-
"""射频模式配置（内联 SPA 页）—— 完全复用主控制台的组件类，外观与 DMR 页一致
   ① 射频通道：6 个通道的模式与频率
   ② 功率调整（RF & Station）：发射功率档位 + 基带收发增益  ← 用户要保留的模块"""

RFMODE_SECTION = r"""
    <section class="page proto-page" id="page-rfmode" style="display:none">

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:var(--green-dim);color:var(--green2)">&#128251;</div>
            <div>
              <div class="card-title">射频模式配置</div>
              <div class="card-sub">6 个射频通道的模式与收发频率（与其它模式页共用同一套频率架构）</div>
            </div>
          </div>
          <span class="card-badge">MMDVM-Multi</span>
        </div>

        <div class="freq-banner">
          <div class="freq-banner-ico">&#128225;</div>
          <div class="freq-banner-txt">
            <b>当前各通道频率 <span style="color:var(--green2);font-size:10.5px;margin-left:6px">统一频率架构</span></b>
            <span id="rfmBanner">读取中…</span>
          </div>
          <button class="btn btn-ghost btn-sm" onclick="rfmLoad()">&#8635; 重新读取</button>
        </div>

        <div id="rfmChans"><div class="sp-empty">正在读取通道…</div></div>

        <div class="mc-foot" style="margin:12px 0 0">
          <button class="btn btn-green btn-sm" onclick="rfmSaveFreq()">&#9889; 保存并应用频率</button>
          <button class="btn btn-ghost btn-sm" onclick="rfmRestartAll()">&#8634; 全部重启</button>
          <span class="hint" id="rfmMsg" style="margin-left:10px"></span>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-head-left">
            <div class="h-ico" style="background:var(--amber-dim);color:var(--amber2)">&#128295;</div>
            <div>
              <div class="card-title">功率调整（RF &amp; Station）</div>
              <div class="card-sub">SX1255 发射功率档位与硬件基带收发增益</div>
            </div>
          </div>
          <span class="card-badge">SX1255</span>
        </div>

        <div class="form-grid">
          <div class="fld"><label>射频发射功率档位 (SX1255)</label>
            <select id="rfmPower">
              <option value="7">大功率 &#8212; 7 级 / +7 dBm / 5 mW（远距离、抗干扰）</option>
              <option value="0">小功率 &#8212; 0 级 / 0 dBm / 1 mW（近距离、更健康）</option>
            </select>
            <div class="hint">切换档位会自动把 TxGain 设为 30dB（大功率）或 10dB（小功率）</div>
          </div>
          <div class="fld"><label>SX1255 硬件基带接收增益 (RxGain 0-60 dB)</label>
            <input id="rfmRxGain" type="number" min="0" max="60" value="0">
            <div class="hint">写入 MMDVM-IQ.ini [Soapy]</div>
          </div>
          <div class="fld"><label>SX1255 硬件基带发射增益 (TxGain 0-40 dB)</label>
            <input id="rfmTxGain" type="number" min="0" max="40" value="30">
            <div class="hint">大功率推荐 30dB / 小功率推荐 10dB</div>
          </div>
        </div>

        <div class="mc-foot" style="margin:12px 0 0">
          <button class="btn btn-green btn-sm" onclick="rfmSavePower()">&#128190; 保存功率设置</button>
          <span class="hint" id="rfmPwrMsg" style="margin-left:10px"></span>
        </div>
      </div>

    </section>
"""
