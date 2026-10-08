/* Mission Control theme: 1969 Apollo MOCR console (Stitch designs, wired to live data). */
(() => {
'use strict';
const { t, n, esc, kfmt, dfmt, nowS } = AW;
const S = AW.state;
const U = s => AW.upper(s);                       // translated labels: locale-aware upper case
const UD = s => String(s ?? '').toUpperCase();     // user data / technical text: plain upper case
let root;
const $ = s => root.querySelector(s);

const SCREWS4 = '<i class="screw tl"></i><i class="screw tr"></i><i class="screw bl"></i><i class="screw br"></i>';
const SCREWS4SM = '<i class="screw sm tl"></i><i class="screw sm tr"></i><i class="screw sm bl"></i><i class="screw sm br"></i>';
const PATCH = `<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
  <circle cx="12" cy="12" r="9" fill="none" stroke="#d18d00" stroke-width="1.2" stroke-dasharray="2 2"/>
  <ellipse cx="12" cy="12" rx="10" ry="4" fill="none" stroke="#ffb000" stroke-width="1" transform="rotate(-30 12 12)"/>
  <circle cx="12" cy="12" r="2.5" fill="#33ff66"/><circle cx="18" cy="8" r="1.8" fill="#ffb000"/>
  <circle cx="5" cy="15" r="1.8" fill="#ffb000"/><circle cx="15" cy="18" r="1.5" fill="#ff7700"/></svg>`;
const ICON = {
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>',
  sync: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12a8 8 0 0 1 14-5.3L20 9"/><path d="M20 4v5h-5"/><path d="M20 12a8 8 0 0 1-14 5.3L4 15"/><path d="M4 20v-5h5"/></svg>',
  table: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 10h18M3 15h18M9 4v16"/></svg>',
  copy: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
};

/* status helpers */
const TAG = { ok: ['[OK]', 'text-green'], running: ['[RUN]', 'text-amber blink'], error: ['[FAIL]', 'text-red'],
              partial: ['[PART]', 'text-amber'], warning: ['[WARN]', 'text-amber'], abandoned: ['[ABRT]', 'text-red'], skipped: ['[SKIP]', 'text-dim'] };
const callWord = st => U(t({ ok: 'mc_nominal', running: 'mc_running', error: 'mc_fail', partial: 'mc_partial', warning: 'mc_caution', abandoned: 'mc_abort', skipped: 'mc_skipped' }[st] || 'mc_nominal'));
const pctState = pct => pct === null || pct === undefined ? { cls: 'st-dim', lamp: '', word: '—' }
  : pct > 50 ? { cls: 'st-green', lamp: 'g', word: U(t('mc_nominal')) } : pct > 20 ? { cls: 'st-amber', lamp: 'a', word: U(t('mc_caution')) }
  : pct > 5 ? { cls: 'st-red', lamp: 'r', word: U(t('mc_warning')) } : { cls: 'st-red', lamp: 'r', word: U(t('mc_critical')) };
const nixie = s => String(s).split('').map(ch => `<span class="nx${/[:/.,]/.test(ch) ? ' dim' : ''}">${esc(ch)}</span>`).join('');
const pad = (v, w) => String(v ?? 0).padStart(w, '0');
const tplus = s => { const m = Math.floor(s / 60), x = s - m * 60; return `T+${pad(m, 2)}:${x.toFixed(1).padStart(4, '0')}`; };

/* ================================================================ top strip */
function topHTML() {
  const run = AW.running().length, caution = AW.recentFailure();
  const page = S.tab;
  return `<header class="mc-top">${SCREWS4SM}
    <div class="mc-patch"><div class="patch-icon">${PATCH}</div>
      <div class="plate" lang="en"><span>AI-WORKERS</span><span class="sep">•</span><span>${esc(U(t('mc_flight_ops')))}</span></div></div>
    <nav class="mc-nav">
      <button class="btn-push${page === 'overview' ? ' lit' : ''}" data-tab="overview"><span class="led"></span>${esc(U(t('tab_overview')))}</button>
      <button class="btn-push${page === 'activity' ? ' lit' : ''}" data-tab="activity"><span class="led"></span>${esc(U(t('tab_activity')))}</button>
    </nav>
    <div class="mc-right">
      <div class="lamp-group">
        <div class="lamp-unit"><div class="lamp ${S.live ? 'green' : 'red'}"></div><span class="lamp-cap">${esc(U(t('mc_link')))}</span></div>
        <div class="lamp-unit"><div class="lamp ${caution ? 'red pulse' : 'red-off'}"></div><span class="lamp-cap">${esc(U(t('mc_master_caution')))}</span></div>
        <div class="lamp-unit"><div class="lamp ${run ? 'amber' : 'amber-off'}"></div><span class="lamp-cap">${esc(U(t('mc_in_flight', { n: run })))}</span></div>
      </div>
      <div class="nixie-cluster" data-role="met"><span class="nixie-label">MET</span>${nixie(new Date().toLocaleTimeString('en-GB'))}</div>
      ${AW.controlsHTML()}
    </div></header>`;
}

/* ================================================================ OVERVIEW */
function dial(pct) {
  /* 0–100 % scale over a 240° sweep; needle = remaining; red/amber zones sit at the EMPTY end */
  const a0 = 210, a1 = -30, cx = 50, cy = 50;
  const at = p => a0 + (a1 - a0) * p / 100;
  const pt = (deg, r) => [cx + r * Math.cos(deg * Math.PI / 180), cy - r * Math.sin(deg * Math.PI / 180)];
  const arc = (p0, p1, r) => { const [x1, y1] = pt(at(p0), r), [x2, y2] = pt(at(p1), r);
    return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 ${Math.abs(at(p0) - at(p1)) > 180 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`; };
  let ticks = '', nums = '';
  for (let i = 0; i <= 100; i += 10) {
    const major = i % 20 === 0, [x1, y1] = pt(at(i), 35), [x2, y2] = pt(at(i), major ? 29 : 32);
    ticks += `<line x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}" stroke="${i <= 10 ? '#ff3b30' : '#7e8590'}" stroke-width="${major ? 1.5 : 1}"/>`;
    if (major) { const [tx, ty] = pt(at(i), 23); nums += `<text x="${tx.toFixed(1)}" y="${(ty + 2.4).toFixed(1)}" text-anchor="middle" font-family="Barlow Condensed, sans-serif" font-size="7" font-weight="600" fill="${i <= 10 ? '#ff6a5f' : '#9aa1ab'}">${i}</text>`; }
  }
  const ang = 90 - at(pct ?? 0);  /* SVG rotate: 0° points up */
  return `<svg class="dial" viewBox="0 0 100 100" aria-hidden="true">
    <circle cx="50" cy="50" r="46" fill="#14171a" stroke="#363b42" stroke-width="3"/>
    <circle cx="50" cy="50" r="43" fill="#0c0e10" stroke="#1f2327" stroke-width="1"/>
    <path d="${arc(0, 100, 36)}" fill="none" stroke="#253029" stroke-width="4"/>
    <path d="${arc(10, 25, 36)}" fill="none" stroke="#ea7600" stroke-width="4"/>
    <path d="${arc(0, 10, 36)}" fill="none" stroke="#ff3b30" stroke-width="4"/>
    ${ticks}${nums}
    <text x="50" y="74" text-anchor="middle" font-family="VT323, monospace" font-size="13" fill="#ffb000" style="filter:drop-shadow(0 0 2px #ffb000)">${pct === null || pct === undefined ? '—' : Math.round(pct) + '%'}</text>
    <text x="50" y="82" text-anchor="middle" font-family="Barlow Condensed, sans-serif" font-size="5" letter-spacing=".8" fill="#6b7280">${esc(U(t('mc_pct_left')))}</text>
    ${pct === null || pct === undefined ? '' : `<g transform="rotate(${ang.toFixed(1)} 50 50)"><line x1="50" y1="54" x2="50" y2="16" stroke="#fff" stroke-linecap="round" stroke-width="2"/></g>`}
    <circle cx="50" cy="50" r="5" fill="#363b42" stroke="#68717d" stroke-width="1"/><circle cx="50" cy="50" r="2" fill="#111417"/></svg>`;
}

function stationCard(m) {
  const unit = { req_day: t('mc_req_day'), tok_min: t('mc_tok_min'), req_min: t('mc_req_min') }[m.unit] || '';
  let name, a, b, pct = m.pct;
  if (m.kind === 'model') { name = `${U(t('mc_station', { p: '' }))}${UD(m.provider)} · ${m.short}`; a = `${m.remaining} / ${m.limit} ${U(unit)}`;
    b = m.free ? U(t('mc_free', { r: m.free.remaining, l: m.free.limit })) : U(t('mc_reset', { t: m.reset })); }
  else if (m.kind === 'provider') { name = `${U(t('mc_station', { p: '' }))}${UD(m.provider)}`; a = `${m.remaining} / ${m.limit} ${U(unit)}`;
    b = U(t('mc_reset', { t: m.reset || '—' })); }
  else if (m.kind === 'window') { name = UD(m.name); a = `${m.remaining} / ${m.limit} ${U(unit)}`; b = U(t('mc_reset', { t: m.reset || '—' })); }
  else if (m.kind === 'nokey') { name = `${U(t('mc_station', { p: '' }))}${UD(m.provider)}`; a = U(t('mc_no_key')); b = U(t('unconfigured')); pct = null; }
  else { name = `${U(t('mc_station', { p: '' }))}${UD(m.provider)}`; a = U(t('requests_n', { n: m.requests })); b = U(t('mc_unlimited')); pct = null; }
  const st = pctState(pct);
  return `<div class="station">
    <div class="dymo ov" title="${esc(name)}">${esc(name)}</div>
    <div class="state">${st.lamp ? `<span class="dotlamp ${st.lamp}${st.lamp !== 'g' ? ' pulse' : ''}"></span>` : ''}<span class="lbl-sm ${st.cls}">${esc(st.word)}</span></div>
    ${dial(pct)}
    <div class="crt-glass"><div class="a glow-a">${esc(a)}</div><div class="b">${esc(b)}</div></div></div>`;
}

function overviewHTML() {
  const m = S.model;
  if (!m) return `<div class="bay col-12">${SCREWS4SM}<div class="select-msg glow-a">${esc(S.usageError ? t('error_prefix', { e: S.usageError }) : U(t('loading')))}</div></div>`;
  const tg = m.tightest, pct = tg ? tg.pct : null, st = pctState(pct), c = m.claude;
  const bingo = pct !== null && pct < 20;
  const label = tg ? (tg.claude ? tg.label : tg.label) : '—';
  const caption = !tg ? U(t('no_live_quota')) : tg.remaining === undefined ? UD(`${label}${tg.resets ? ' · ' + U(t('mc_resets', { t: tg.resets })) : ''}`)
    : UD(`${label} · ${n(tg.remaining)} / ${n(tg.limit)}`);
  const stations = m.meters.filter(x => x.kind !== 'group');
  const ladders = c.limits.length ? c.limits.map(L => {
    const on = Math.round(L.pct / 5);
    return `<div class="ladder"><div class="ladder-body">${Array.from({ length: 20 }, (_, i) => `<span class="${i < on ? 'on' + (L.pct <= 20 ? ' low' : '') : ''}"></span>`).join('')}</div>
      <div class="plate ov"><b>${esc(UD(L.label))} ${L.pct}%</b><small>${esc(U(t('mc_resets', { t: L.resets || '—' })))}</small></div></div>`;
  }).join('') : `<div class="glow-a" style="font-family:var(--f-crt);font-size:12px;padding:20px 0">${esc(U(c.pending ? t('mc_fetching') : c.error ? t('live_error', { e: c.error }) : t('no_live_quota')))}</div>`;
  const status = c.pending ? U(t('mc_fetching')) : c.error ? U(t('mc_fail')) : U(t('mc_cache_active'));
  const span24 = (c.spans || []).find(s => s.span === '24h');
  return `
  <div class="ov-grid">
    <section class="bay col-5 hero">${SCREWS4SM}
      <div class="bay-hdr"><div class="plate ov">${esc(U(t('mc_tightest_hdr')))}</div><span class="dymo ov">SYS-PRI-01</span></div>
      <div class="nixie-tube hero-tube">
        <div class="top"><span class="lbl-sm" style="color:rgba(245,158,11,.8)">${esc(U(t('mc_reserve')))}</span><span class="lbl-sm ${st.cls}">${esc(U(t('mc_status', { s: st.word })))}</span></div>
        <div class="hero-digits"><span class="nixie-digit big${pct !== null && pct <= 20 ? ' red' : ''}">${pct === null ? '--' : Math.round(pct)}</span><span class="nixie-digit pct">%</span></div>
        <div class="plate ov hero-caption">${esc(caption)}</div>
      </div>
      <div class="annunciator${bingo ? ' on' : ''}"><div class="l"><span class="sq"></span>${esc(U(t('mc_bingo')))}</div>
        <div class="r">${esc(U(bingo ? t('mc_bingo_on') : t('mc_bingo_off')))}</div></div>
      <div class="counters">
        <div class="counter"><span class="lbl-sm">${esc(U(t('mc_req_today')))}</span><div class="nixie-tube"><span class="nixie-digit">${pad(m.kpis.req, 4)}</span></div><span class="sub">${esc(U(t('mc_sub_workers')))}</span></div>
        <div class="counter"><span class="lbl-sm">${esc(U(t('mc_tok_today')))}</span><div class="nixie-tube"><span class="nixie-digit">${pad(m.kpis.tok, 6)}</span></div><span class="sub">${esc(U(t('mc_sub_inout')))}</span></div>
        <div class="counter fail"><span class="lbl-sm">${esc(U(t('mc_failed')))}</span><div class="nixie-tube"><span class="nixie-digit red">${pad(m.kpis.fail, 4)}</span></div><span class="sub">${esc(U(t('mc_sub_today')))}</span></div>
      </div>
    </section>
    <section class="bay col-7">${SCREWS4SM}
      <div class="bay-hdr"><div class="plate ov">${esc(U(t('mc_fd_hdr')))}</div><span class="dymo ov">MON-BAY-FDIR</span></div>
      <div class="fd-grid">
        <div class="cryo"><div class="cryo-hdr"><span class="lbl-sm" style="color:var(--ov-on-var)">${esc(U(t('mc_cryo')))}</span><span class="lbl-sm" style="color:var(--ov-tertiary)">O2/H2 SYNC</span></div>
          <div class="cryo-bars">${ladders}</div></div>
        <div class="crt-glass telemetry-crt">
          <div class="hd"><span class="glow-a">${esc(U(t('mc_telemetry')))}</span><span class="dotlamp a pulse"></span></div>
          <div class="rows glow-a">
            <div><span>${esc(U(t('mc_req_today')))}</span><b>${n(c.calls)}</b></div>
            <div><span>${esc(U(t('kpi_out_tok')))}</span><b>${kfmt(c.out)}</b></div>
            <div><span>${esc(U(t('kpi_cache')))}</span><b>${kfmt(c.cacheRead)}</b></div>
            <div><span>${esc(U(t('kpi_sessions')))}</span><b>${n(c.sessions)}</b></div>
            <div class="rule">${'-'.repeat(60)}</div>
            <div class="small"><span>${esc(U(t('mc_source')))}</span><span>CLAUDE -P /USAGE</span></div>
            <div class="small"><span>${esc(U(t('mc_status_lbl')))}</span><span class="${c.error ? 'text-red' : 'glow-g'}">${esc(status)}</span></div>
          </div>
          <div class="ft"><span>${c.age !== undefined && c.age !== null ? esc(U(t('mc_measured', { t: AW.ago(nowS() - c.age) }))) : '—'}</span>
            <span>${span24 ? esc(U(t('mc_anthropic', { n: n(span24.requests) }))) : ''}</span></div>
        </div>
      </div>
    </section>
    <section class="bay col-12">${SCREWS4SM}
      <div class="bay-hdr"><div class="plate ov">${esc(U(t('mc_stations_hdr')))}</div>
        <div style="display:flex;align-items:center;gap:8px"><span class="dymo ov">${esc(U(t('mc_bank', { n: stations.length })))}</span>
          <span class="lbl-sm" style="color:${m.notes.length ? 'var(--amber)' : 'var(--ov-tertiary)'}">${esc(U(t(m.notes.length ? 'mc_bus_bad' : 'mc_bus')))}</span></div></div>
      <div class="stations">${stations.map(stationCard).join('')}</div>
      ${m.notes.length ? `<div class="ov-notes">${m.notes.map(x => `<div class="caution-strip amber"><span class="lamp dot amber"></span><span>${esc(x)}</span></div>`).join('')}</div>` : ''}
      ${S.showTable ? tableHTML(m) : ''}
    </section>
    <section class="bay col-12 ctl-row">${SCREWS4SM}
      <div class="group"><div class="plate ov">${esc(U(t('mc_actuators')))}</div>
        <button class="sq-btn btn-unlit" data-act="refresh" title="${esc(t('refresh'))}">${ICON.refresh}<span>${esc(U(t('mc_refresh')))}</span></button>
        <button class="sq-btn ${S.auto ? 'btn-lit' : 'btn-unlit'}" data-act="auto" title="${esc(S.auto ? t('auto_on') : t('auto_off'))}">${ICON.sync}<span>${esc(U(t('mc_autoref')))}</span></button>
        <button class="sq-btn ${S.showTable ? 'btn-lit' : 'btn-unlit'}" data-act="table" title="${esc(t('table_view'))}">${ICON.table}<span>${esc(U(t('mc_tbl')))}</span></button></div>
      <div class="louvers"><span></span><span></span><span></span></div>
      <div class="group"><div class="flip-cap"><span class="dymo ov" style="font-size:9px;padding:2px 6px">${esc(U(t('mc_interlock')))}</span>
          <small>${esc(U(S.paused ? t('mc_held') : t('mc_failsafe')))}</small></div>
        <div class="flip${S.paused ? ' on' : ''}" data-act="pause" title="${esc(t('pause_title'))}"><div class="flip-guard"><small>${esc(U(t('mc_armed')))}</small><div class="lever"></div><i></i></div>
          <div class="t">${esc(U(t('mc_feed_hold')))}</div></div></div>
    </section>
  </div>`;
}

function tableHTML(m) {
  const rows = m.workers.map(w => {
    const rq = (w.quota && w.quota.requests) || {};
    return `<tr><td>${esc(UD(w.provider))}</td><td>${esc(U(w.ready ? t('ready') : t('no_key')))}</td><td>${n(w.today_requests)}</td>
      <td>${n(w.today_tokens_in)}</td><td>${n(w.today_tokens_out)}</td><td>${n(rq.remaining)}</td><td>${n(rq.limit)}</td></tr>`
      + (w.models || []).map(md => { const dq = (md.quota || {}).day_requests || {};
        return `<tr><td>&nbsp;&nbsp;↳ ${esc(md.model)}</td><td>${esc(U(t('local_counter')))}</td><td>${n(md.requests_today)}</td><td colspan="2">${n(md.tokens_today)}</td><td>${n(dq.remaining)}</td><td>${n(dq.limit)}</td></tr>`; }).join('');
  }).join('');
  return `<div class="crt-glass" style="margin-top:12px;padding:8px"><table class="ov-table glow-a"><thead><tr>
    <th>${esc(U(t('th_provider')))}</th><th>${esc(U(t('th_status')))}</th><th>${esc(U(t('th_requests')))}</th><th>${esc(U(t('th_in')))}</th>
    <th>${esc(U(t('th_out')))}</th><th>${esc(U(t('th_remaining')))}</th><th>${esc(U(t('th_limit')))}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

/* ================================================================ ACTIVITY */
function stripHTML() {
  const run = AW.running();
  if (!run.length) return `<div class="crt-casing in-flight-cell"><div class="crt in-flight-crt idle">▶ ${esc(U(t('mc_idle')))}<span class="blink">_</span></div></div>`;
  return run.slice(0, 2).map((c, i) => {
    const [role, ...rest] = String(c.title || '').split(': ');
    const more = i === 1 && run.length > 2 ? ` +${run.length - 2}` : '';
    return `<div class="crt-casing in-flight-cell"><div class="crt in-flight-crt" data-call="${c.id}">
      <div><span class="${i ? 'text-amber' : 'text-green'}">▶ ${esc(UD(c.tool))}</span>
        <span style="color:#e6a100;margin-left:6px">${esc(rest.length ? role + ':' : '')}</span> <span style="color:#ffc84d">${esc(rest.length ? rest.join(': ') : role)}</span></div>
      <div><span class="jobs">${c.jobs_done}/${c.jobs || 1} ${esc(U(AW.tp('jobs_n', c.jobs || 1, { n: '' }).trim()))}</span>
        <span class="text-dim" style="margin:0 8px">${esc((c.active || [])[0] || '')}</span>
        <span class="${i ? 'text-amber' : 'text-green'}" data-tplus="${c.ts}">${tplus(nowS() - c.ts)}</span><span class="blink">_</span>${esc(more)}</div>
    </div></div>`;
  }).join('');
}

function logRow(c) {
  const [tag, cls] = TAG[c.status] || TAG.ok;
  const meta = [UD(AW.who(c)), c.project, c.jobs ? `${c.jobs_done}/${c.jobs} ${U(t('mc_jobs'))}` : null, c.providers[0] || null,
    (c.tokens.in || c.tokens.out) ? `${kfmt(c.tokens.in)}→${kfmt(c.tokens.out)} TOK` : null,
    c.status === 'running' ? null : UD(dfmt(c.dur))].filter(Boolean).join(' · ');
  return `<div class="log-row${c.id === S.sel ? ' sel' : ''}" data-call="${c.id}">
    <div class="l1"><span><span class="${cls}">${tag}</span> <b class="${c.id === S.sel ? '' : 'text-amber'}" lang="en">${esc(c.tool)}</b> · ${esc(c.title || '')}</span>
      <span class="${c.status === 'running' ? 'text-amber' : 'text-dim'}">${AW.tfmt(c.ts)}</span></div>
    <div class="l2">${esc(meta)}${c.status === 'running' ? ` · <span data-tplus="${c.ts}">${tplus(nowS() - c.ts)}</span>` : ''}${c.failed_attempts ? ` · <span class="text-red">${c.failed_attempts}× FAIL</span>` : ''}</div></div>`;
}

function leftBayHTML() {
  const f = S.filters, opts = AW.filterOptions();
  const knob = key => {
    const i = Math.max(0, opts[key].indexOf(f[key])), deg = -120 + (opts[key].length > 1 ? 240 * i / (opts[key].length - 1) : 0);
    return `<button class="rotary-item${f[key] ? ' set' : ''}" data-knob="${key}" title="${esc(AW.filterLabel(key, f[key]))}">
      <div class="rotary-knob" style="transform:rotate(${deg.toFixed(0)}deg)"></div><span class="rotary-label">${esc(U(t({ status: 'f_status', tool: 'f_tool', prov: 'f_provider' }[key])))}</span></button>`;
  };
  const rows = AW.filtered(), fresh = AW.freshCount();
  const active = ['status', 'tool', 'prov'].filter(k => f[k]).map(k => UD(AW.filterLabel(k, f[k])));
  return `<div class="hdr-bar"><div class="dymo">${esc(U(t('mc_call_log')))}</div>
      <div class="rotary-controls">${knob('status')}${knob('tool')}${knob('prov')}
        <div class="guarded-toggle${S.paused ? ' on' : ''}" data-act="pause" title="${esc(t('pause_title'))}"><div class="toggle-guard"><div class="toggle-lever"></div></div>
          <span class="rotary-label">${esc(U(t('mc_hold')))}</span></div></div></div>
    <div class="crt-casing log-casing"><div class="crt log-crt">
      <div class="crt-cmd-bar"><label>${esc(U(t('mc_search')))} <input data-role="search" value="${esc(f.q)}" spellcheck="false" autocomplete="off"></label>
        <span class="meta">${active.length ? esc('FLT: ' + active.join(' · ')) + ' // ' : 'CH 01 // '}${esc(U(t('mc_frames', { n: rows.length })))}</span></div>
      <div class="log-rows" data-role="rows">${fresh ? `<div class="newbar" data-act="resume">${esc(U(AW.tp('new_calls', fresh)))}</div>` : ''}${
        !S.shown.length ? `<div class="log-empty">${esc(U(t('empty_title')))}<br>${esc(t('empty_txt'))}</div>`
        : !rows.length ? `<div class="log-empty">${esc(U(t('nomatch_title')))}</div>` : rows.map(logRow).join('')}</div>
    </div></div>`;
}

function trajectorySVG(d) {
  const st = AW.stations(d).slice(0, 4), sess = d.session || {};
  const G = '#33ff66', A = '#ffb000', R = '#ff3b30', DIM = '#664d14';
  const col = s => s === 'ok' ? G : s === 'error' ? R : s === 'running' ? A : DIM;
  const ys = st.length <= 1 ? [70] : st.map((_, i) => 26 + i * (90 / (st.length - 1)));
  const callCol = d.status === 'ok' ? G : ['error', 'abandoned'].includes(d.status) ? R : A;
  let paths = `<path d="M 80 70 L 250 70" fill="none" stroke="${st.length || d.status === 'ok' ? G : A}" stroke-width="2.5" filter="url(#mc-glow-g)"/><polygon points="248,67 256,70 248,73" fill="${G}"/>`;
  let nodes = '';
  st.forEach((g, i) => {
    const y = ys[i], c = col(g.status), dashed = g.status === 'skipped' || g.status === 'running';
    paths += `<path d="M 290 70 C 330 70, 350 ${y}, 420 ${y}" fill="none" stroke="${c}" stroke-width="${dashed ? 1.8 : 2.5}"${dashed ? ' stroke-dasharray="4 4"' : ' filter="url(#mc-glow-g)"'}/>`;
    if (!dashed) paths += `<polygon points="418,${y - 3} 426,${y} 418,${y + 3}" fill="${c}"/>`;
    if (g.status === 'ok') paths += `<path d="M 580 ${y} C 650 ${y}, 660 70, 712 70" fill="none" stroke="${G}" stroke-width="2.5" filter="url(#mc-glow-g)"/><polygon points="710,67 718,70 710,73" fill="${G}"/>`;
    const stat = U(t({ ok: 'mc_ok', error: 'mc_fail', skipped: 'mc_skipped', running: 'mc_running' }[g.status]));
    const tok = g.tin || g.tout ? ` · ${kfmt(g.tin)}→${kfmt(g.tout)} TOK` : '';
    nodes += `<g transform="translate(505, ${y})">
      <rect x="-78" y="-15" width="156" height="30" fill="${g.status === 'ok' ? '#061f0e' : '#100d08'}" stroke="${c}" stroke-width="${g.status === 'ok' ? 2 : 1.5}"${g.status === 'ok' ? ' filter="url(#mc-glow-g)"' : ' stroke-dasharray="3 3"'} rx="2"/>
      <circle cx="-62" cy="0" r="4" fill="${c}"/>
      <text x="6" y="-2" text-anchor="middle" fill="${g.status === 'ok' ? G : '#a67c2e'}" font-family="Chakra Petch" font-size="10.5" font-weight="700" letter-spacing="1">${esc(U(t('mc_station', { p: '' })) + UD(g.provider))}</text>
      <text x="6" y="10" text-anchor="middle" fill="${g.status === 'ok' ? '#b3ffca' : c}" font-family="Share Tech Mono" font-size="8.5">${esc(`${g.model || ''} · ${stat}${tok}`)}</text></g>`;
  });
  if (!st.length && d.status !== 'running') paths += `<path d="M 290 70 L 712 70" fill="none" stroke="${callCol}" stroke-width="2" stroke-dasharray="2 4"/>`;
  const more = AW.stations(d).length - st.length;
  return `<svg class="trajectory-svg" viewBox="0 0 830 140" preserveAspectRatio="xMidYMid meet">
    <defs><filter id="mc-glow-g" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
    <ellipse cx="415" cy="70" rx="380" ry="60" fill="none" stroke="rgba(255,176,0,0.07)" stroke-dasharray="4 4"/>
    <line x1="40" y1="70" x2="790" y2="70" stroke="rgba(255,176,0,0.1)" stroke-dasharray="2 4"/>
    ${paths}
    <g transform="translate(60, 70)"><circle r="16" fill="#140f04" stroke="${A}" stroke-width="2"/><circle r="5" fill="${A}" filter="url(#mc-glow-g)"/>
      <text y="-22" text-anchor="middle" fill="${A}" font-family="Chakra Petch" font-size="10" font-weight="700" letter-spacing="1">${esc(U(t('mc_flight_director')))}</text>
      <text y="29" text-anchor="middle" fill="#a17719" font-family="Share Tech Mono" font-size="9.5">${esc([AW.who(sess), sess.cwd ? sess.cwd.split('/').pop() : null].filter(Boolean).join(' · '))}</text></g>
    <g transform="translate(270, 70)"><rect x="-16" y="-16" width="32" height="32" fill="#140f04" stroke="${G}" stroke-width="2" rx="3" filter="url(#mc-glow-g)"/>
      <text y="4" text-anchor="middle" fill="${G}" font-family="Share Tech Mono" font-size="12" font-weight="700">λ</text>
      <text y="-22" text-anchor="middle" fill="${G}" font-family="Chakra Petch" font-size="10" font-weight="700" letter-spacing="1">AI-WORKERS</text>
      <text y="29" text-anchor="middle" fill="#2bd858" font-family="Share Tech Mono" font-size="9.5">${esc(`${d.tool} · ${AW.jobs(d).length ? AW.tp('jobs_n', AW.jobs(d).length) : t('local')}`)}</text></g>
    ${nodes}
    ${more > 0 ? `<text x="505" y="136" text-anchor="middle" fill="#a17719" font-family="Share Tech Mono" font-size="9">+${more}</text>` : ''}
    <g transform="translate(745, 70)"><polygon points="0,-16 16,10 -16,10" fill="#140f04" stroke="${callCol}" stroke-width="2" filter="url(#mc-glow-g)"/>
      <text y="-22" text-anchor="middle" fill="${callCol}" font-family="Chakra Petch" font-size="10" font-weight="700" letter-spacing="1">${esc(U(t('mc_return')))}</text>
      <text y="27" text-anchor="middle" fill="${callCol}" font-family="Share Tech Mono" font-size="9.5">${esc(d.status === 'running' ? U(t('result_pending')) : `${AW.who(sess)} · ${d.result ? t('chars_n', { n: n(d.result.size) }) : AW.stLabel(d.status)}`)}</text></g>
  </svg>`;
}

function tapeRow(j, d, tall) {
  const atts = AW.attempts(j), t0 = d.ts, span = Math.max((d.end || nowS()) - t0, 0.001);
  const H = tall ? 64 : 18, B = H / 2, AMP = tall ? 22 : 7, WAVE = tall ? 5 : 2.2;
  const X = ts => 4 + (ts - t0) / span * 292;
  let svg = `<line x1="0" y1="${B}" x2="300" y2="${B}" stroke="rgba(51,255,102,.12)" stroke-width="1"/>`;
  for (const a of (atts.length ? atts : [j])) {
    const x1 = X(a.ts), x2 = Math.max(x1 + 3, X(a.end || nowS()));
    if (a.status === 'error') { const m = Math.max(x1 + 6, (x1 + x2) / 2);
      svg += `<polyline points="${x1},${B} ${m - 5},${B} ${m - 2},${B - AMP} ${m + 2},${B + AMP} ${m + 5},${B} ${Math.max(x2, m + 8)},${B}" fill="none" stroke="#ff3b30" stroke-width="1.8" style="filter:drop-shadow(0 0 2px #ff3b30)"/>`;
      const code = AW.httpCode(a.error);
      if (tall) svg += `<text x="${m}" y="9" text-anchor="middle" fill="#ff3b30" font-family="Share Tech Mono" font-size="8.5">${esc(code ? 'HTTP ' + code : 'ERR')}</text>`;
      svg += `<line x1="${x2}" y1="0" x2="${x2}" y2="${H}" stroke="rgba(255,176,0,.3)" stroke-dasharray="2 4"/>`; }
    else if (a.status === 'skipped') svg += `<line x1="${x1}" y1="${B}" x2="${x2 + 8}" y2="${B}" stroke="#664d14" stroke-width="1.6" stroke-dasharray="3 3"/>`
      + (tall ? `<text x="${x1 + 2}" y="${B - 6}" fill="#7a5410" font-family="Share Tech Mono" font-size="8">${esc(UD(t('s_skipped')))}</text>` : '');
    else { const c = a.status === 'running' ? '#ffb000' : '#33ff66'; let pts = '';
      for (let x = x1, i = 0; x <= x2; x += tall ? 9 : 6, i++) pts += `${x.toFixed(1)},${(B + (i % 3 === 1 ? -WAVE : i % 3 === 2 ? WAVE * .6 : 0)).toFixed(1)} `;
      svg += `<polyline points="${pts}${x2.toFixed(1)},${B}" fill="none" stroke="${c}" stroke-width="2" style="filter:drop-shadow(0 0 2px ${c})"/>`; }
  }
  const used = atts.filter(a => a.status === 'ok').pop() || atts[atts.length - 1];
  const tin = atts.reduce((s, a) => s + ((a.tokens || {}).in || 0), 0), tout = atts.reduce((s, a) => s + ((a.tokens || {}).out || 0), 0);
  const cur = AW.currentJob();
  return `<div class="tape-row${cur && cur.id === j.id ? ' sel' : ''}" data-job="${j.id}">
    <div class="lbl"><b>${esc(j.label || t('job_word'))}</b> · ${esc(j.role || '—')}</div>
    <div class="sub">${esc([used ? used.model : null, j.status === 'running' ? null : UD(dfmt(j.dur)), tin || tout ? `${kfmt(tin)}→${kfmt(tout)}` : null].filter(Boolean).join(' · '))}</div>
    <svg viewBox="0 0 300 ${H}" preserveAspectRatio="none" style="height:${H}px">${svg}</svg></div>`;
}

function stageHTML(d) {
  const job = AW.currentJob(), atts = job ? AW.attempts(job) : AW.attempts(d), a = AW.currentAttempt();
  const callTabs = [['request', t('mc_t_request')], ['result', t('mc_t_result')]];
  const SHORT = { system: t('mc_tab_system'), input: t('mc_tab_input'), response: t('mc_tab_response'), error: t('mc_tab_error'), raw: t('mc_tab_raw') };
  const aTabs = a ? AW.attemptTabs(a).map(([k]) => [k, SHORT[k]]) : [];
  const allTabs = [callTabs[0], ...aTabs, callTabs[1]];
  let tab = S.ptab.mc;
  if (!allTabs.some(([k]) => k === tab)) tab = a ? AW.attemptTab(a) : 'request';
  const idx = a ? atts.indexOf(a) : -1;
  const sess = d.session || {};
  let head = '', notes = '', body = '', size = null, payload = null;
  if (tab === 'request' || tab === 'result') {
    head = `<span><b>${esc(U(tab === 'request' ? t('request_from', { w: AW.who(sess) }) : (sess.source === 'cli' ? t('returned_cli') : t('returned_claude'))))}</b></span>
      <span class="text-dim">${esc(UD(`${d.tool} · ${AW.stLabel(d.status)}`))}</span>`;
    if (tab === 'request') {
      const req = d.args || {}, keys = ['task', 'input', 'prompt', 'system', 'items'];
      const params = Object.entries(req).filter(([k]) => !keys.includes(k));
      const pk = keys.find(k => req[k]);
      payload = pk ? req[pk] : null;
      body = (params.length ? `<dl class="params">${params.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(Array.isArray(v) ? v.join(', ') : typeof v === 'string' ? v : JSON.stringify(v))}</dd>`).join('')}</dl>` : '')
        + (payload ? `<div class="ph">// ${esc(UD(pk))}:</div>` : '');
    } else {
      payload = d.result || null;
      if (d.error) notes += `<div class="caution-strip"><span class="lamp dot red"></span><span>${esc(d.error)}</span></div>`;
      if (!payload) body = `<div class="ph">${esc(U(d.status === 'running' ? t('still_running') : t('no_result')))}</div>`;
    }
  } else if (a) {
    const tok = a.tokens || {};
    head = `<span><b>${esc(U(t('mc_station', { p: '' })) + UD(a.provider))}</b> · ${esc(a.model || '')} · <span class="${a.status === 'ok' ? 'text-green' : a.status === 'running' ? 'text-amber' : 'text-red'}">${esc(callWord(a.status))}</span></span>
      <span class="text-dim">${esc([a.finish_reason ? UD(t('finish', { r: a.finish_reason })) : null, a.status === 'running' ? null : UD(dfmt(a.dur)), tok.in || tok.out ? `${tok.in || 0}→${tok.out || 0} TOK` : null].filter(Boolean).join(' · '))}</span>`;
    for (const nt of a.notes || []) notes += `<div class="caution-strip"><span class="lamp dot red"></span><span><b>${esc(UD(AW.noteText(nt)))}</b></span></div>`;
    if (a.status === 'skipped') notes += `<div class="caution-strip amber"><span class="lamp dot amber"></span><span>${esc(UD(t('skipped_why', { e: a.error })))}</span></div>`;
    if (a.warning) notes += `<div class="caution-strip"><span class="lamp dot red"></span><span>${esc(a.warning)}</span></div>`;
    if (a.truncated) notes += `<div class="caution-strip amber"><span class="lamp dot amber"></span><span>${esc(UD(t('truncated_note', { n: a.max_tokens })))}</span></div>`;
    payload = AW.attemptPayload(a, tab);
    if (!payload) body = `<div class="ph">${esc(U(t('no_system')))}</div>`;
  }
  if (payload) {
    const x = AW.payloadText(payload); size = x.size;
    PAY = payload;
    body += `<div class="ph">${esc(U(t('mc_payload_hdr', { n: n(x.size) })))}</div><pre class="${S.wrap ? '' : 'nowrap'}">${esc(x.text) || '<span class="text-dim">' + esc(t('empty')) + '</span>'}</pre>`
      + (x.partial ? `<div style="margin-top:6px"><button class="btn-push" data-act="full" data-ref="${x.ref}">${esc(U(t('load_full')))}</button>
        <span class="text-dim"> ${esc(t('showing_first', { a: n(x.text.length), b: n(x.size) }))}</span></div>` : '');
  } else PAY = null;
  const title = tab === 'request' || tab === 'result' ? U(t('mc_stage_call')) : U(t('mc_stage', { a: idx + 1, b: atts.length }));
  return `<div class="stage-dymo-row"><div class="dymo">${esc(title)}</div>
      ${atts.length > 1 && a ? `<div class="stage-nav"><button class="btn-push" data-att-step="-1"${idx <= 0 ? ' disabled' : ''}>◀</button><button class="btn-push" data-att-step="1"${idx >= atts.length - 1 ? ' disabled' : ''}>▶</button></div>` : ''}</div>
    <div class="crt-casing" style="flex:1;min-height:0;display:flex"><div class="crt attempt-crt" style="flex:1">
      <div><div class="stage-hdr">${head}</div>${notes}
        <div class="tab-row">${allTabs.map(([k, l]) => `<div class="tab-sq${k === tab ? ' lit' : ''}" data-stage-tab="${k}">${k === tab ? '● ' : ''}${esc(U(l))}</div>`).join('')}</div></div>
      <div class="payload-box">${body}</div>
      <div class="stage-foot"><span>${size !== null ? esc(U(t('chars_n', { n: n(size) }))) : ''}</span>
        <span><button class="btn-push" data-act="wrap">${esc(U(S.wrap ? t('nowrap') : t('wrap')))}</button>
        ${payload ? `<button class="btn-push" data-act="copy">${esc(U(t('copy')))}</button>` : ''}</span></div>
    </div></div>`;
}
let PAY = null;

function rightBayHTML() {
  const d = S.detail;
  if (!d) return `<div class="crt-casing" style="flex:1;display:flex"><div class="crt select-msg glow-a" style="flex:1">${S.detailMissing
    ? esc(U(t('not_found'))) + '<br>' + esc(t('not_found_txt')) : esc(U(t('select_title'))) + '<br><span class="text-dim">' + esc(t('select_txt')) + '</span>'}</div></div>`;
  const s = d.summary, idx = S.calls.length - S.calls.findIndex(c => c.id === d.id);
  const lampCls = d.status === 'ok' ? 'green' : d.status === 'running' ? 'amber' : ['error', 'abandoned'].includes(d.status) ? 'red' : 'amber';
  const lampCol = { green: '#33ff66', amber: '#ffb000', red: '#ff3b30' }[lampCls];
  const path = d.status === 'ok' ? 'mc_path_ok' : d.status === 'running' ? 'mc_path_run' : 'mc_path_fail';
  const dur = d.status === 'running' ? nowS() - d.ts : d.dur || 0;
  return `<div class="hdr-bar"><div style="display:flex;align-items:center;gap:10px;min-width:0">
      <div class="dymo" style="font-size:12px;overflow:hidden;text-overflow:ellipsis" title="${esc(d.title || '')}">${esc(`${U(t('mc_call', { n: pad(idx > 0 && idx <= S.calls.length ? idx : 0, 3) }))} // ${UD(d.title || d.tool)}`)}</div>
      <div style="display:flex;align-items:center;gap:5px;background:#0d1c10;border:1px solid ${lampCol}55;padding:2px 8px;border-radius:2px;flex:none">
        <div class="lamp dot ${lampCls}"></div><span style="font-size:10px;font-weight:700;color:${lampCol};letter-spacing:1px">${esc(callWord(d.status))}</span></div></div>
      <button class="btn-push" data-act="copy-json" style="padding:3px 10px;font-size:10px;flex:none">${ICON.copy} ${esc(U(t('copy_json')))}</button></div>
    <div class="crt-casing schematic-casing"><div class="crt schematic-crt">
      <div class="crt-hdr"><span>${esc(U(t('mc_traj')))}</span><span class="${d.status === 'ok' ? 'text-green' : d.status === 'running' ? 'text-amber' : 'text-red'}">${esc(U(t(path)))}</span></div>
      ${trajectorySVG(d)}</div></div>
    <div class="nixie-counter-bar">
      <div class="nixie-cell"><div class="plate">${esc(U(t('mc_duration')))}</div><div class="nixie-cluster" data-role="dur">${nixie(dur < 100 ? pad(dur.toFixed(1), 4) : Math.round(dur))}<span class="unit">S</span></div></div>
      <div class="nixie-cell"><div class="plate">${esc(U(t('mc_jobs')))}</div><div class="nixie-cluster">${nixie(`${s.jobs_done}/${s.jobs}`)}</div></div>
      <div class="nixie-cell"><div class="plate">${esc(U(t('mc_attempts')))}</div><div class="nixie-cluster">${nixie(pad(s.attempts, 2))}${s.failed_attempts ? `<span class="note">(${esc(U(t('n_failed_p', { n: s.failed_attempts }).replace(/[()]/g, '')))})</span>` : ''}${s.skipped_attempts ? `<span class="note">(${esc(U(t('n_skipped_p', { n: s.skipped_attempts }).replace(/[()]/g, '')))})</span>` : ''}</div></div>
      <div class="nixie-cell"><div class="plate">${esc(U(t('mc_tok_in')))}</div><div class="nixie-cluster">${nixie(pad(s.tokens.in, 4))}</div></div>
      <div class="nixie-cell"><div class="plate">${esc(U(t('mc_tok_out')))}</div><div class="nixie-cluster">${nixie(pad(s.tokens.out, 4))}</div></div>
    </div>
    <div class="detail-split">
      <div class="chart-bay"><div class="dymo">${esc(U(t('mc_timeline')))}</div>
        <div class="crt-casing" style="flex:1;min-height:0;display:flex"><div class="crt chart-crt" style="flex:1">
          <div class="chart-hdr"><span>${esc(U(t('mc_tape', { d: UD(dfmt(dur)) })))}</span><span class="${d.status === 'running' ? 'text-amber blink' : 'text-green'}">${esc(U(t(d.status === 'running' ? 'mc_recording' : 'mc_sync')))}</span></div>
          <div class="strip-tape">${AW.jobs(d).map((j, _, all) => tapeRow(j, d, all.length <= 4)).join('') || `<div class="text-dim" style="font-size:11px">${esc(U(t('no_request')))}</div>`}</div>
        </div></div></div>
      <div class="attempt-bay">${stageHTML(d)}</div>
    </div>`;
}

const SCREWS6 = '<i class="screw tl"></i><i class="screw tr"></i><i class="screw bl"></i><i class="screw br"></i><i class="screw ml"></i><i class="screw mr"></i>';

function footHTML() {
  const left = S.tab === 'activity'
    ? [S.settings ? U(t('mc_retention', { d: S.settings.retention_days, mb: S.settings.max_mb, n: S.calls.length })) : '',
       S.storage !== null ? U(t('mc_storage', { s: AW.mb(S.storage) })) : ''].filter(Boolean).join(' · ')
    : [U(t('mc_footer_src')), S.model ? U(t('as_of', { t: new Date(S.model.generated * 1000).toLocaleTimeString(AW.locale()) })) : ''].filter(Boolean).join(' · ');
  return `<footer class="bottom-strip"><div class="fl"><span class="plate">CONSOLE BAY C-4</span><span class="ft">${esc(left)}</span></div>
    <div class="vent-grille">${'<div class="vent-slot"></div>'.repeat(8)}</div>
    <div class="fr"><span class="dotlamp ${S.live ? 'g' : 'r'}"></span><span style="color:${S.live ? '#33ff66' : '#ff3b30'}">${esc(U(t(S.live ? 'mc_stable' : 'mc_lost')))}</span>
      <span style="color:#ffb000">•</span><span>${esc(U(t('mc_houston')))} / ${esc(new Date().toLocaleDateString(AW.locale()))}</span></div></footer>`;
}

function activityBody() {
  return `<section class="telemetry-strip" data-role="strip">${stripHTML()}</section>
    <main class="main-bays">
      <aside class="bay-left" data-role="left">${leftBayHTML()}</aside>
      <section class="bay-right" data-role="right">${rightBayHTML()}</section>
    </main>`;
}

/* both pages share one console frame: top strip and bottom strip never move between tabs */
function shellHTML() {
  return `<div class="mc"><div class="console-frame mc-shell">${SCREWS6}${topHTML()}
    ${S.tab === 'overview' ? `<div class="mc-scroll" data-role="scroll"><div class="mc-ov" data-role="page">${overviewHTML()}</div></div>`
      : `<div class="mc-page" data-role="page">${activityBody()}</div>`}
    ${footHTML()}</div></div>`;
}

/* ================================================================ render + events */
function swap(sel, html) { const el = $(sel); if (el) el.outerHTML = html; }

function render(what) {
  if (what === 'all' || what === 'tab' || !$('.mc-shell') || $('.mc-shell').dataset.page !== S.tab) {
    root.innerHTML = shellHTML();
    $('.mc-shell').dataset.page = S.tab;
    return;
  }
  swap('.mc-top', topHTML());
  swap('.bottom-strip', footHTML());
  if (S.tab === 'overview') {
    if (what === 'usage' || what === 'calls' || what === 'live') {
      const sc = $('[data-role="scroll"]'), y = sc.scrollTop;
      $('[data-role="page"]').innerHTML = overviewHTML();
      sc.scrollTop = y;
    }
    return;
  }
  if (what === 'calls' || what === 'live') {
    const rows = $('[data-role="rows"]'), keep = rows ? rows.scrollTop : 0;
    const focused = document.activeElement && document.activeElement.dataset.role === 'search';
    const caret = focused ? document.activeElement.selectionStart : null;
    $('[data-role="left"]').innerHTML = leftBayHTML();
    $('[data-role="rows"]').scrollTop = keep;
    if (focused) { const q = $('[data-role="search"]'); q.focus(); q.setSelectionRange(caret, caret); }
    $('[data-role="strip"]').innerHTML = stripHTML();
  }
  if (what === 'detail') {
    const box = $('.payload-box'), keep = box ? box.scrollTop : 0, tape = $('.strip-tape'), tk = tape ? tape.scrollTop : 0;
    $('[data-role="right"]').innerHTML = rightBayHTML();
    const nb = $('.payload-box'); if (nb) nb.scrollTop = keep;
    const nt = $('.strip-tape'); if (nt) nt.scrollTop = tk;
  }
}

function tick() {
  const met = root.querySelector('[data-role="met"]');
  if (met) met.innerHTML = `<span class="nixie-label">MET</span>${nixie(new Date().toLocaleTimeString('en-GB'))}`;
  for (const el of root.querySelectorAll('[data-tplus]')) el.textContent = tplus(nowS() - +el.dataset.tplus);
  if (S.tab === 'activity' && S.detail && S.detail.status === 'running') {
    const dur = $('[data-role="dur"]'), x = nowS() - S.detail.ts;
    if (dur) dur.innerHTML = `${nixie(x < 100 ? pad(x.toFixed(1), 4) : Math.round(x))}<span class="unit">S</span>`;
  }
}

AW.views.apollo = {
  mount(r) {
    root = r;
    AW.bindControls(root);
    root.addEventListener('click', async e => {
      const tab = e.target.closest('[data-tab]'); if (tab) return AW.setTab(tab.dataset.tab);
      const knob = e.target.closest('[data-knob]'); if (knob) return AW.cycleFilter(knob.dataset.knob);
      const job = e.target.closest('[data-job]'); if (job) return AW.selectJob(job.dataset.job);
      const st = e.target.closest('[data-stage-tab]'); if (st) return AW.setPtab('mc', st.dataset.stageTab);
      const step = e.target.closest('[data-att-step]');
      if (step) { const j = AW.currentJob(), atts = j ? AW.attempts(j) : [], a = AW.currentAttempt(), i = atts.indexOf(a) + +step.dataset.attStep;
        if (atts[i]) AW.selectAttempt(atts[i].id); return; }
      const call = e.target.closest('[data-call]'); if (call) return AW.select(call.dataset.call);
      const el = e.target.closest('[data-act]'); if (!el) return;
      const act = el.dataset.act;
      if (act === 'refresh') AW.refreshUsage();
      else if (act === 'auto') AW.setAuto(!S.auto);
      else if (act === 'table') AW.toggleTable();
      else if (act === 'pause') AW.setPaused(!S.paused);
      else if (act === 'resume') AW.setPaused(false);
      else if (act === 'wrap') AW.toggleWrap();
      else if (act === 'copy' && PAY) AW.copyPayload(PAY);
      else if (act === 'copy-json') AW.copy(JSON.stringify(S.detail, null, 2));
      else if (act === 'full') { el.disabled = true; el.textContent = AW.upper(t('loading_btn'));
        try { await AW.loadBlob(el.dataset.ref); } catch (err) { el.textContent = AW.upper(t('unavailable')); } }
    });
    root.addEventListener('input', e => { if (e.target.dataset.role === 'search') AW.setFilter('q', e.target.value); });
  },
  unmount() { root = null; },
  update(what) {
    if (!root) return;
    if (what === 'tick') return tick();
    render(what);
  },
};
})();
