/* Synthwave theme: outrun neon HUD (Stitch designs, wired to live data). */
(() => {
'use strict';
const { t, n, esc, kfmt, dfmt, nowS } = AW;
const S = AW.state;
const U = s => AW.upper(s);
let root;
const $ = s => root.querySelector(s);

const I = {
  terminal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M5 7l5 5-5 5"/><path d="M12 17h7"/></svg>',
  hub: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M6.4 6.4l3.5 3.5M17.6 6.4l-3.5 3.5M6.4 17.6l3.5-3.5M17.6 17.6l-3.5-3.5" stroke="currentColor" stroke-width="1.6"/></svg>',
  brain: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 3a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V4a3 3 0 0 0-3-1zm6 0a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1V4a3 3 0 0 1 3-1z"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L1 21h22L12 2zm0 6l1 7h-2l1-7zm0 9a1.3 1.3 0 1 1 0 2.6A1.3 1.3 0 0 1 12 17z"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>',
  rows: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M3 12h18M3 18h18"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>',
  pause: '<svg viewBox="0 0 24 24" fill="currentColor" width="10" height="10"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor" width="10" height="10"><path d="M7 5v14l12-7z"/></svg>',
  sensors: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="2"/><path d="M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h16"/><path d="M15 8l4 4-4 4"/></svg>',
  fork: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12h6l4-5h6"/><path d="M10 12l4 5h6"/><path d="M17 4l3 3-3 3"/></svg>',
  tree: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><rect x="3" y="3" width="6" height="5"/><rect x="15" y="10" width="6" height="5"/><rect x="15" y="17" width="6" height="4"/><path d="M6 8v11h9M6 12h9"/></svg>',
  wf: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/></svg>',
  code: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 7l-5 5 5 5M16 7l5 5-5 5"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>',
};

const dotFor = st => ({ ok: 'bg-mint', running: 'bg-sec led-pulse', error: 'bg-pri-c sh-pri', abandoned: 'bg-pri-c sh-pri', partial: 'bg-orange sh-orange', warning: 'bg-orange sh-orange', skipped: 'bg-out' }[st] || 'bg-out');
const stColor = st => ({ ok: 'c-mint', running: 'c-sec', error: 'c-pri-c', abandoned: 'c-pri-c', partial: 'c-orange', warning: 'c-orange' }[st] || 'c-out');
const fmtTplus = s => { const m = Math.floor(s / 60), x = s - m * 60; return `${String(m).padStart(2, '0')}:${x.toFixed(1).padStart(4, '0')}`; };

function leds(pct, hot) {
  const on = pct === null || pct === undefined ? 0 : Math.round(pct / 100 * 16);
  return `<div class="leds">${Array.from({ length: 16 }, (_, i) => {
    if (i >= on) return '<span></span>';
    if (hot && i >= 10) return '<span class="p"></span>';
    if (hot && i >= 8) return '<span class="v"></span>';
    return `<span class="c${hot ? '' : ' g'}"></span>`;
  }).join('')}</div>`;
}

/* ================================================================ top bar */
/* identical on both tabs: only the active state changes, nothing moves */
function topHTML() {
  const run = AW.running().length, act = S.tab === 'activity';
  const tab = (key, on) => `<button data-tab="${key}" class="${on ? 'on' : ''}"><span class="dot d6 tdot"></span>${esc(U(t('tab_' + key)))}${key === 'activity' ? `<span class="count"${run ? '' : ' hidden'}>${run}</span>` : ''}</button>`;
  return `<header class="sw-top">
    <div class="sw-brand">
      <div class="sw-logo cy">${I.hub}<span class="badge"${run ? '' : ' hidden'}></span></div>
      <div style="display:flex;flex-direction:column"><span class="name chrome" lang="en">AI-WORKERS</span>
        <span class="sub t-lbl-sm c-out up">${esc(U(t('sw_subtitle')))}</span></div>
    </div>
    <nav class="sw-nav">${tab('overview', !act)}${tab('activity', act)}</nav>
    <div class="sw-right">
      <div class="sw-live${S.live ? '' : ' off'}"><span class="ring"><i></i></span>
        <span class="t-lbl-md" style="font-weight:700;letter-spacing:.08em">${esc(U(S.live ? t('sw_live_tel') : t('reconnecting')))}</span></div>
      ${AW.controlsHTML()}
    </div></header>`;
}

/* ================================================================ OVERVIEW */
function meterRowHTML(m, sub) {
  const st = AW.statusOf(m.pct), hot = m.pct !== null && m.pct <= 20 || m.kind === 'window' && m.pct !== null && m.pct < 97;
  const name = m.kind === 'model' ? `↳ ${m.name}` : m.name;
  const unitLbl = { req_day: t('daily_requests'), tok_min: t('sw_tok_min'), req_min: t('per_min_requests') }[m.unit] || '';
  const desc = m.kind === 'model'
    ? [m.free ? t('free_left', { r: n(m.free.remaining), l: n(m.free.limit) }) : null, m.minReq ? t('min_req', { u: n(m.minReq.used), l: n(m.minReq.limit) }) : null, t('reset_cap', { t: m.reset })].filter(Boolean).join(' · ')
    : [m.source, m.reset ? t('reset_cap', { t: m.reset }) : null].filter(Boolean).join(' · ');
  const badge = m.pct === null ? `<span class="badge-st none">${esc(st.label)}</span>`
    : `<span class="badge-st ${st.key}">${esc(st.key === 'good' ? `${st.label} ${Math.round(m.pct)}%` : `${Math.round(m.pct)}%`)}</span>`;
  const colCls = hot ? 'c-pri' : 'c-sec';
  if (sub) return `<div class="meter-row sub"><div class="top"><div><span class="t-code c-on" style="font-weight:600">${esc(name)}</span>
      <span class="t-lbl-sm c-var" style="display:block">${esc(desc)}</span></div>
      <div class="r"><span class="t-lbl-md ${colCls}" style="font-weight:600">${n(m.remaining)} / ${n(m.limit)}</span><span class="t-lbl-sm c-var" style="display:block">${esc(unitLbl)}</span></div></div>
    <div class="bot">${leds(m.pct, hot)}${badge}</div></div>`;
  return `<div class="meter-row${hot ? ' hot' : ''}"><div class="top"><div>
      <div class="name"><span class="sq ${hot ? 'bg-pri-c sh-pri' : m.kind === 'provider' ? 'bg-sec sh-sec' : 'bg-ter'}"></span><span class="t-h-sm ${hot ? 'c-pri' : 'c-on'}" style="font-weight:700">${esc(name)}</span></div>
      <span class="t-lbl-sm c-var" style="display:block;margin-top:2px">${esc(desc)}</span></div>
      <div class="r"><span class="t-lbl-md ${colCls}" style="font-weight:600">${n(m.remaining)} / ${n(m.limit)}</span><span class="t-lbl-sm c-var" style="display:block">${esc(unitLbl)}</span></div></div>
    <div class="bot">${leds(m.pct, hot)}${badge}</div></div>`;
}

function workersHTML(m) {
  let html = '';
  for (const x of m.meters) {
    if (x.kind === 'group') {
      html += `<div class="grp-box"><div class="grp-hdr"><div class="l"><span class="sq bg-ter" style="box-shadow:0 0 6px #ae71ff"></span>
        <span class="t-h-sm c-ter" style="font-weight:700">${esc(x.provider)}${x.tier ? ` (${esc(x.tier)})` : ''}</span></div>
        <span class="t-lbl-sm c-var">${esc([x.source, t('req_today', { n: x.requests }), t('models_n', { n: x.models })].join(' · '))}</span></div>
        ${x.children.map(c => meterRowHTML(c, true)).join('')}</div>`;
    } else if (x.kind === 'model') continue;
    else if (x.kind === 'provider' || x.kind === 'window') html += meterRowHTML(x, false);
    else if (x.kind === 'nokey') html += `<div class="meter-row"><div class="top"><div class="name"><span class="sq bg-out"></span><span class="t-h-sm c-on">${esc(x.provider)}</span></div>
      <span class="badge-st none">${esc(t('no_key'))}</span></div></div>`;
    else html += `<div class="meter-row"><div class="top"><div><div class="name"><span class="sq bg-ter"></span><span class="t-h-sm c-on" style="font-weight:700">${esc(x.provider)}</span></div>
      <span class="t-lbl-sm c-var">${esc(x.source)}</span></div><div class="r"><span class="t-lbl-md c-sec">${esc(t('requests_n', { n: n(x.requests) }))}</span>
      <span class="t-lbl-sm c-var" style="display:block">${esc(t('limit_undefined'))}</span></div></div></div>`;
  }
  return html;
}

function overviewHTML() {
  const m = S.model;
  if (!m) return `<main class="sw-main"><div class="sw-hero panel-cyber"><span class="t-lbl-md c-sec">${esc(S.usageError ? t('error_prefix', { e: S.usageError }) : t('loading'))}</span></div></main>`;
  const tg = m.tightest, st = AW.statusOf(tg ? tg.pct : null), c = m.claude;
  const reqCap = m.meters.filter(x => x.unit === 'req_day' && x.limit).reduce((s, x) => s + x.limit, 0);
  const win = m.meters.find(x => x.kind === 'window' && x.unit === 'tok_min');
  const failRatio = m.kpis.req ? m.kpis.fail / m.kpis.req : 0;
  const stations = m.meters.filter(x => !['group'].includes(x.kind)).length;
  const label = !tg ? t('no_live_quota') : tg.remaining === undefined ? t('tightest_of', { p: tg.label }) : t('tightest_left', { p: tg.label, r: n(tg.remaining), l: n(tg.limit) });
  const bar = (pct, cls, glow) => `<div class="bar"><i class="${cls}" style="width:${Math.max(0, Math.min(100, pct)).toFixed(0)}%;box-shadow:0 0 6px ${glow}"></i></div>`;
  return `<main class="sw-main">
    <section class="sw-hero panel-cyber">
      <div><span class="t-lbl-sm c-sec up" style="display:flex;align-items:center;gap:4px;letter-spacing:.1em;margin-bottom:4px"><span class="c-pri" style="width:12px;height:12px;display:inline-flex">${I.warn}</span>${esc(U(t('sw_tightest')))}</span>
        <div style="display:flex;align-items:baseline;gap:12px"><span class="num neon-dual">${tg ? Math.round(tg.pct) + '%' : '—'}</span>
          ${tg ? `<span class="t-lbl-md c-pri" style="background:rgba(255,78,123,.2);padding:2px 8px;border:1px solid var(--primary-c);border-radius:4px">${esc(U(st.label))}</span>` : ''}</div>
        <p class="t-lbl-md c-var" style="margin:8px 0 0">${esc(label)}${tg && tg.resets ? ` · ${esc(t('resets_at', { t: '' }).trim())} <span class="c-sec" style="font-weight:600">${esc(tg.resets)}</span>` : ''}</p></div>
      <div class="kpis">
        <div class="kpi-pod"><span class="t-lbl-sm c-var up">${esc(U(t('sw_req_traffic')))}</span>
          <div class="row"><span class="t-h-lg c-on">${n(m.kpis.req)}</span><span class="t-lbl-sm c-sec">${esc(U(t('sw_today')))}</span></div>${bar(reqCap ? m.kpis.req / reqCap * 100 : 0, 'bg-sec', '#6ff2ff')}</div>
        <div class="kpi-pod"><span class="t-lbl-sm c-var up">${esc(U(t('sw_tok_consumption')))}</span>
          <div class="row"><span class="t-h-lg c-on">${n(m.kpis.tok)}</span><span class="t-lbl-sm c-sec">${esc(U(t('sw_tokens')))}</span></div>${bar(win && win.pct !== null ? 100 - win.pct : 0, 'bg-ter', '#ae71ff')}</div>
        <div class="kpi-pod${m.kpis.fail ? ' alert box-pink' : ''}"><span class="t-lbl-sm ${m.kpis.fail ? 'c-pri' : 'c-var'} up" style="display:flex;justify-content:space-between">${esc(U(t('sw_failure_audit')))}${m.kpis.fail ? '<span class="dot d6 bg-pri-c led-pulse"></span>' : ''}</span>
          <div class="row"><span class="t-h-lg ${m.kpis.fail ? 'c-pri neon-pink' : 'c-on'}">${esc(U(t('sw_n_failed', { n: n(m.kpis.fail) })))}</span><span class="t-lbl-sm ${m.kpis.fail ? 'c-pri' : 'c-var'}">${esc(U(t(m.kpis.fail ? 'sw_anomaly' : 'sw_clear')))}</span></div>${bar(failRatio * 100, 'bg-pri-c', '#ff4e7b')}</div>
      </div>
    </section>
    <div class="sw-toolbar"><div class="grp">
        <button class="btn" data-act="refresh">${I.refresh}<span>${esc(U(t('refresh')))}</span></button>
        <button class="btn${S.auto ? ' cy' : ' dim'}" data-act="auto"><span class="dot d8 ${S.auto ? 'led-pulse' : ''}" style="background:${S.auto ? '#11dbea' : '#5c3f43'};box-shadow:${S.auto ? '0 0 6px #11dbea' : 'none'}"></span><span>${esc(U(S.auto ? t('auto_on') : t('auto_off')))}</span></button>
        <button class="btn${S.showTable ? ' cy' : ' dim'}" data-act="table">${I.rows}<span>${esc(U(t('table_view')))}</span></button></div>
      <div class="t-lbl-md c-var" style="display:flex;align-items:center;gap:8px"><span class="dot d6 bg-out"></span><span>${esc(U(t('sw_hud_sync')))}</span>
        <span class="c-sec neon-cyan" style="font-weight:600">${esc(t('as_of', { t: new Date(m.generated * 1000).toLocaleTimeString(AW.locale()) }))}</span></div></div>
    ${S.showTable ? `<div class="sw-panel panel-cyber">${tableHTML(m)}</div>` : ''}
    <div class="sw-split">
      <div class="sw-panel panel-cyber">
        <div class="sw-panel-hdr"><div class="l"><span class="c-sec">${I.hub}</span><h2 class="t-h-sm c-on" style="margin:0;font-weight:700;letter-spacing:.08em">${esc(U(t('h_workers')))}</h2></div>
          <span class="chip-b">${esc(U(t('sw_nodes', { n: stations })))}</span></div>
        ${workersHTML(m)}
        ${m.notes.length ? `<div class="sw-notes">${m.notes.map(x => `<div class="note-strip"><span style="width:14px;height:14px;display:inline-flex">${I.warn}</span>${esc(x)}</div>`).join('')}</div>` : ''}
      </div>
      <div class="sw-panel panel-cyber" style="gap:16px">
        <div class="sw-panel-hdr"><div class="l"><span class="c-pri neon-pink">${I.brain}</span><h2 class="t-h-sm c-pri neon-pink" style="margin:0;font-weight:700;letter-spacing:.08em">${esc(U(t('h_claude')))}</h2></div>
          <span class="chip-b pk"><span class="dot d6 bg-pri-c led-pulse"></span>${esc(U(t('sw_operator')))}</span></div>
        <div class="claude-meters">${c.limits.length ? c.limits.map((L, i) => `<div class="cm"><div class="hd">
            <span class="t-body-md c-on" style="font-weight:600;display:flex;align-items:center;gap:6px;font-family:var(--f-mono)"><span class="dot d6 ${i ? 'bg-pri-c sh-pri' : 'bg-sec sh-sec'}"></span>${esc(L.label.charAt(0).toLocaleUpperCase(AW.locale()) + L.label.slice(1))}</span>
            <span class="t-lbl-md ${i ? 'c-pri neon-pink' : 'c-sec'}" style="font-weight:700">${esc(t('pct_left', { p: L.pct }))}${L.resets ? ' · ' + esc(t('resets_at', { t: L.resets })) : ''}</span></div>
            <div class="track"><i style="width:${L.pct}%;background:${i ? 'linear-gradient(90deg,#ae71ff,#ff4e7b)' : '#6ff2ff'};box-shadow:0 0 ${i ? '12px #ff4e7b' : '10px #6ff2ff'}"></i></div></div>`).join('')
          : `<span class="t-lbl-md c-var">${esc(c.pending ? t('live_pending') : c.error ? t('live_error', { e: c.error }) : t('no_live_quota'))}</span>`}</div>
        <div class="kcards">
          <div class="kcard"><span class="t-lbl-sm c-var up" style="display:block">${esc(U(t('sw_daily_volume')))}</span><div style="margin-top:4px"><span class="t-h-md c-on" style="font-weight:700">${n(c.calls)}</span><span class="t-lbl-sm c-sec" style="display:block;margin-top:2px">${esc(t('kpi_req_today'))}</span></div></div>
          <div class="kcard"><span class="t-lbl-sm c-var up" style="display:block">${esc(U(t('sw_stream_gen')))}</span><div style="margin-top:4px"><span class="t-h-md c-sec neon-cyan" style="font-weight:700">${kfmt(c.out)}</span><span class="t-lbl-sm c-var" style="display:block;margin-top:2px">${esc(t('kpi_out_tok'))}</span></div></div>
          <div class="kcard"><span class="t-lbl-sm c-var up" style="display:block">${esc(U(t('sw_cache_tel')))}</span><div style="margin-top:4px"><span class="t-h-md c-ter" style="font-weight:700">${kfmt(c.cacheRead)}</span><span class="t-lbl-sm c-var" style="display:block;margin-top:2px">${esc(t('kpi_cache'))}</span></div></div>
          <div class="kcard"><span class="t-lbl-sm c-var up" style="display:block">${esc(U(t('sw_engagements')))}</span><div style="margin-top:4px"><span class="t-h-md c-on" style="font-weight:700">${n(c.sessions)}</span><span class="t-lbl-sm c-sec" style="display:block;margin-top:2px">${esc(t('kpi_sessions'))}</span></div></div>
        </div>
        <div class="footnote t-lbl-sm c-var"><div style="display:flex;align-items:center;gap:8px"><span class="dot d8 led-pulse" style="background:#11dbea;box-shadow:0 0 8px #11dbea"></span>
          <span>${esc(t('sw_live_from'))} <span class="c-sec">claude -p /usage</span></span></div>
          <span class="c-ter" style="background:var(--cont);padding:2px 8px;border-radius:4px;border:1px solid var(--outline-var)">${esc(c.age !== undefined && c.age !== null ? t('live_ago', { n: Math.round(c.age / 60) }).replace(/^.*· /, '') : t('sw_cache2'))}</span></div>
      </div>
    </div>
    <footer class="sw-foot panel-cyber"><div class="t-lbl-sm c-var" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span class="c-sec" style="font-weight:700">ai-workers &gt;_</span><span>${esc(t('sw_footer'))}</span></div>
      <div style="display:flex;align-items:center;gap:8px" class="t-lbl-sm"><span class="dot d6 bg-sec"></span><span class="c-sec" style="font-weight:600">SYNTHWAVE HUD</span></div></footer>
  </main>`;
}

function tableHTML(m) {
  const rows = m.workers.map(w => {
    const rq = (w.quota && w.quota.requests) || {};
    return `<tr><td>${esc(w.provider)}</td><td>${esc(w.ready ? t('ready') : t('no_key'))}</td><td>${n(w.today_requests)}</td><td>${n(w.today_tokens_in)}</td>
      <td>${n(w.today_tokens_out)}</td><td>${n(rq.remaining)}</td><td>${n(rq.limit)}</td><td>${esc(AW.bx(w.source))}</td></tr>` + (w.models || []).map(md => {
      const dq = (md.quota || {}).day_requests || {};
      return `<tr><td>&nbsp;&nbsp;↳ ${esc(md.model)}</td><td>${esc(t('local_counter'))}</td><td>${n(md.requests_today)}</td><td colspan="2">${n(md.tokens_today)}</td><td>${n(dq.remaining)}</td><td>${n(dq.limit)}</td><td>${esc(t('daily_requests'))}</td></tr>`;
    }).join('');
  }).join('');
  return `<table class="sw-table"><thead><tr><th>${t('th_provider')}</th><th>${t('th_status')}</th><th>${t('th_requests')}</th><th>${t('th_in')}</th>
    <th>${t('th_out')}</th><th>${t('th_remaining')}</th><th>${t('th_limit')}</th><th>${t('th_source')}</th></tr></thead><tbody>${rows}</tbody></table>`;
}

/* ================================================================ ACTIVITY */
function tickerHTML() {
  const run = AW.running();
  const chips = run.map((c, i) => `<div class="tchip${i % 2 ? ' pk' : ''} t-lbl-sm" data-call="${c.id}">
      <span class="dot d6 ${i % 2 ? 'bg-pri-c' : 'bg-sec'} led-pulse"></span>
      <span class="${i % 2 ? 'c-pri-c' : 'c-sec'} up" lang="en">${esc(c.tool)}</span><span class="c-on">${esc(c.title || '')}</span>
      ${c.jobs > 1 ? `<span class="prog"><i style="width:${(c.jobs_done / c.jobs * 100).toFixed(0)}%"></i></span><span class="c-sec-fixed">${esc(t('jobs_x', { d: c.jobs_done, t: c.jobs }))}</span>` : ''}
      ${(c.active || [])[0] ? `<span class="c-out">·</span><span class="c-var">${esc(c.active[0])}</span>` : ''}
      <span class="${i % 2 ? 'c-pri' : 'c-sec'}" style="font-weight:700" data-tplus="${c.ts}">${fmtTplus(nowS() - c.ts)}</span></div>`).join('');
  const today = S.calls.filter(c => new Date(c.ts * 1000).toDateString() === new Date().toDateString());
  const tok = today.reduce((s, c) => s + c.tokens.in + c.tokens.out, 0);
  return `<div class="ticker t-lbl-sm"><div class="chips"><div class="lead">${I.sensors}${esc(U(t('sw_now_running', { n: run.length })))}</div>
      ${chips || `<span class="c-out">${esc(t('idle'))}</span>`}</div>
    <div class="right"><span class="c-out">${esc(U(t('sw_today_calls')))}</span><span class="c-mint glow-cyan" style="font-weight:700">${esc(t('sw_calls_tok', { c: today.length, t: kfmt(tok) }))}</span></div></div>`;
}

function feedItem(c) {
  const bad = ['error', 'abandoned'].includes(c.status), sel = c.id === S.sel;
  const pv = c.providers[0] || '';
  return `<div class="fitem${sel ? ' sel' : ''}${bad ? ' failed' : ''}" data-call="${c.id}">
    <div class="l1"><div class="l"><span class="dot d8 ${dotFor(c.status)}"></span><span class="t-lbl-sm up ${bad ? 'c-pri-c' : 'c-out'}" lang="en">${esc(c.tool)}</span>
      <span class="title t-body-md ${bad ? 'c-pri' : 'c-on'}">${esc(c.title || '')}</span></div><span class="t-lbl-sm c-out">${AW.tfmt(c.ts)}</span></div>
    <div class="l2 t-lbl-sm c-var"><span class="${sel ? 'c-sec' : 'c-on'}" style="font-weight:500">${esc(AW.who(c))}${c.project ? ' · ' + esc(c.project) : ''}</span>
      ${c.jobs ? `<span>·</span><span class="${stColor(c.status)}">${esc(t('jobs_x', { d: c.jobs_done, t: c.jobs }))}</span>` : ''}
      ${pv ? `<span>·</span><span class="${pv.startsWith('gemini') ? 'c-ter' : pv.startsWith('groq') ? 'c-pri' : 'c-out'}">${esc(pv)}</span>` : ''}
      ${c.tokens.in || c.tokens.out ? `<span>·</span><span>${kfmt(c.tokens.in)} → ${kfmt(c.tokens.out)} ${t('tok')}</span>` : ''}
      <span class="dur ${bad ? 'c-pri-c' : c.status === 'running' ? 'c-sec' : 'c-sec-fixed'}">${c.status === 'running' ? `<span data-since="${c.ts}">${dfmt(nowS() - c.ts)}</span>` : esc(dfmt(c.dur))}${c.failed_attempts ? ` [${c.failed_attempts}×]` : ''}</span></div></div>`;
}

function feedHTML() {
  const rows = AW.filtered(), fresh = AW.freshCount(), opts = AW.filterOptions();
  const sel = key => `<select class="fsel" data-filter="${key}">${opts[key].map(v => `<option value="${esc(v)}"${v === S.filters[key] ? ' selected' : ''}>${esc(AW.filterLabel(key, v))}</option>`).join('')}</select>`;
  const cap = S.settings ? S.settings.max_mb * 1e6 : null;
  return `<div class="feed-bar">
      <div class="search">${I.search}<input data-role="search" placeholder="${esc(t('search_ph'))}" value="${esc(S.filters.q)}" spellcheck="false" autocomplete="off"><kbd>/</kbd></div>
      <div class="filters-row"><div class="l">${sel('status')}${sel('tool')}${sel('prov')}</div>
        <button class="btn pk t-lbl-sm" data-act="pause" style="padding:2px 8px;font-size:9px">${S.paused ? I.play : I.pause}${esc(U(S.paused ? t('paused') : t('pause')))}</button></div></div>
    <div class="feed-list" data-role="rows">${fresh ? `<div class="newbar" data-act="resume">${esc(AW.tp('new_calls', fresh))}</div>` : ''}${
      !S.shown.length ? `<div class="empty"><b>${esc(t('empty_title'))}</b>${esc(t('empty_txt'))}</div>`
      : !rows.length ? `<div class="empty"><b>${esc(t('nomatch_title'))}</b>${esc(t('nomatch_txt'))}</div>` : rows.map(feedItem).join('')}</div>
    <div class="feed-foot t-lbl-sm"><span>${esc([rows.length === S.shown.length ? t('calls_n', { n: S.shown.length }) : t('calls_of', { a: rows.length, b: S.shown.length }),
        S.settings ? t('kept', { d: S.settings.retention_days, mb: S.settings.max_mb }) : null].filter(Boolean).join(' · '))}</span>
      ${S.storage !== null ? `<span style="display:flex;align-items:center;gap:8px">${esc(U(t('sw_storage', { s: AW.mb(S.storage) })))}<span class="bar"><i style="width:${cap ? Math.min(100, S.storage / cap * 100).toFixed(1) : 0}%"></i></span></span>` : ''}</div>`;
}

function targetHTML(a, k) {
  const tok = a.tokens || {};
  const cls = { ok: 'ok', error: 'bad', skipped: 'skip', running: 'run' }[a.status] || 'bad';
  const code = AW.httpCode(a.error);
  const badge = a.status === 'ok' ? '200 OK' : a.status === 'skipped' ? U(t('s_skipped')) : a.status === 'running' ? U(t('s_running')) : code ? `HTTP ${code}` : U(t('error_word'));
  const sel = AW.currentAttempt() === a;
  return `<div class="target ${cls}" data-att="${a.id}" style="${sel ? 'outline:1px solid rgba(111,242,255,.5);outline-offset:2px' : ''}"><div>
      <div class="t-lbl-sm ${a.status === 'ok' ? 'c-mint' : a.status === 'running' ? 'c-sec' : 'c-pri-c'}" style="font-weight:${a.status === 'ok' ? 700 : 500}">${esc(a.provider)} / ${esc(a.model || '')}</div>
      <div class="t-lbl-sm c-out">${esc([t('sw_attempt', { n: k }), a.status === 'running' ? null : dfmt(a.dur), tok.in || tok.out ? `${kfmt(tok.in)}→${kfmt(tok.out)} ${t('tok')}` : null].filter(Boolean).join(' · '))}</div></div>
    <span class="tbadge ${cls}">${esc(badge)}</span></div>`;
}

function dagHTML(d) {
  const sess = d.session || {}, atts = AW.attempts(d), jobs = AW.jobs(d);
  const fb = jobs.filter(j => j.fallback).length + (atts.some(a => a.status === 'skipped') && !jobs.some(j => j.fallback) ? 1 : 0);
  const shown = atts.length > 6 ? atts.filter(a => a.status !== 'ok').slice(0, 3).concat(atts.filter(a => a.status === 'ok').slice(0, 3)) : atts;
  const bad = ['error', 'abandoned'].includes(d.status);
  return `<div class="dag"><div style="display:flex;align-items:center;justify-content:space-between">
      <span class="t-lbl-sm c-ter-fixed up" style="font-weight:700;letter-spacing:.08em;display:flex;align-items:center;gap:4px">${I.tree}${esc(U(t('sw_dag')))}</span>
      <span class="t-lbl-sm c-out">${esc(U(fb ? t('sw_fallbacks', { n: fb }) : t('sw_direct')))}</span></div>
    <div class="dag-row">
      <div class="node src"><div class="k">${esc(U(t('sw_source')))}</div><div class="v">${esc(AW.who(sess))}</div><div class="s">${esc(sess.cwd ? t('sw_workspace', { p: sess.cwd.split('/').pop() }) : sess.client || '—')}</div></div>
      <div class="dag-arrow c-sec">${I.arrow}</div>
      <div class="node dsp"><div class="k">${esc(U(t('sw_dispatch')))}</div><div class="v">ai-workers</div><div class="s">${esc(`${d.tool} · ${jobs.length ? AW.tp('jobs_n', jobs.length) : t('local')}`)}</div></div>
      ${atts.length ? `<div class="dag-arrow c-pri-c">${I.fork}</div><div class="targets">${shown.map(a => targetHTML(a, atts.indexOf(a) + 1)).join('')}
        ${atts.length > shown.length ? `<div class="t-lbl-sm c-out">+${atts.length - shown.length}</div>` : ''}</div>` : ''}
      <div class="dag-arrow c-orange">${I.arrow}</div>
      <div class="node ret${bad ? ' bad' : ''}"><div class="k">${esc(U(t('sw_return')))}</div><div class="v">${esc(t('result_to', { w: AW.who(sess) === 'CLI' ? 'CLI' : 'Claude' }))}</div>
        <div class="s">${esc(d.status === 'running' ? t('result_pending') : d.result ? t('chars_n', { n: n(d.result.size) }) : AW.stLabel(d.status))}</div></div>
    </div></div>`;
}

function waterfallHTML(d) {
  const atts = AW.attempts(d), jobs = AW.jobs(d);
  if (!jobs.length) return '';
  const t0 = d.ts, span = Math.max((d.end || nowS()) - t0, 0.001);
  const L = ts => Math.max(0, (ts - t0) / span * 100), W = (a, b) => Math.max(1, (b - a) / span * 100);
  const cur = AW.currentAttempt();
  const rows = jobs.flatMap(j => {
    const ja = AW.attempts(j);
    if (!ja.length) return [`<div class="wgrid row"><div class="job"><span class="dot d6 bg-out"></span><span class="c-on">${esc(j.label || t('job_word'))}</span></div>
      <div class="c-out">${esc(j.role || '—')}</div><div></div><div class="r c-out">${esc(dfmt(j.dur))}</div><div class="r c-out">—</div></div>`];
    return ja.map((a, i) => {
      const tok = a.tokens || {}, bad = a.status === 'error', prev = ja[i - 1];
      const wait = prev && prev.end && a.ts - prev.end > 0.05 ? `<i class="wait" style="left:${L(prev.end).toFixed(2)}%;width:${W(prev.end, a.ts).toFixed(2)}%"></i>` : '';
      return `<div class="wgrid row${cur === a ? ' sel' : ''}${bad ? ' bad' : ''}" data-att="${a.id}">
        <div class="job"><span class="dot d6 ${dotFor(a.status)}"></span><span class="c-on" style="${a.status === 'ok' ? 'font-weight:600' : ''}">${esc(j.label || t('job_word'))}</span>
          ${ja.length > 1 ? `<span class="${a.status === 'ok' ? 'c-sec' : 'c-out'}">(${esc(t('sw_att_short', { n: i + 1 }))})</span>` : ''}</div>
        <div class="${a.status === 'ok' ? 'c-on' : 'c-out'}">${esc(j.role || '—')} → <span class="${a.status === 'ok' ? 'c-sec' : bad ? 'c-pri-c' : 'c-out'}" style="font-weight:${a.status === 'ok' ? 700 : 400}">${esc(a.provider + '/' + (a.model || ''))}</span></div>
        <div><div class="wlane">${wait}<i class="${a.status}" style="left:${L(a.ts).toFixed(2)}%;width:${W(a.ts, a.end || nowS()).toFixed(2)}%" title="${esc(AW.stLabel(a.status) + ' · ' + dfmt(a.dur ?? nowS() - a.ts))}"></i></div></div>
        <div class="r ${bad ? 'c-pri-c' : a.status === 'ok' ? 'c-sec' : 'c-out'}" style="font-weight:700">${a.status === 'running' ? `<span data-since="${a.ts}">${dfmt(nowS() - a.ts)}</span>` : esc(dfmt(a.dur))}</div>
        <div class="r ${a.status === 'ok' ? 'c-mint' : 'c-out'}">${tok.in || tok.out ? `${kfmt(tok.in)} → ${kfmt(tok.out)}` : '0 → 0'}</div></div>`;
    });
  }).join('');
  return `<div style="display:flex;flex-direction:column;gap:8px"><div class="wf-hdr"><h2 class="t-lbl-md">${I.wf}${esc(U(t('sw_waterfall', { n: jobs.length })))}</h2>
      <span class="t-lbl-sm c-out">${esc(U(t('sw_span', { d: dfmt(span) })))}</span></div>
    <div class="wtable"><div class="wgrid head"><div>${esc(U(t('sw_col_job')))}</div><div>${esc(U(t('col_model')))}</div><div>${esc(U(t('sw_col_wf')))}</div><div class="r">${esc(U(t('col_time')))}</div><div class="r">${esc(U(t('col_tokens')))}</div></div>${rows}</div></div>`;
}

let PAY = [];
function termHTML(title, p, extra = '') {
  if (!p) return '';
  const x = AW.payloadText(p), key = PAY.push(p) - 1;
  return `<div class="term"><div class="term-bar"><span class="l">${I.code}${esc(title)} · ${esc(t('chars_n', { n: n(x.size) }))}</span>
      <span class="r"><button data-act="wrap">${esc(S.wrap ? t('nowrap') : t('wrap'))}</button><span>·</span>
        <button class="cy" data-act="copy" data-p="${key}" style="display:flex;align-items:center;gap:4px">${I.copy}${esc(t('copy'))}</button></span></div>
    <div class="term-body">${extra}<p class="prompt">ai-workers &gt;_</p><pre class="${S.wrap ? '' : 'nowrap'}">${esc(x.text) || `<span class="c-out">${esc(t('empty'))}</span>`}</pre>
      ${x.partial ? `<div class="more">${esc(t('showing_first', { a: n(x.text.length), b: n(x.size) }))} <button class="btn cy" data-act="full" data-ref="${x.ref}" style="padding:1px 8px">${esc(t('load_full'))}</button></div>` : ''}</div></div>`;
}

function attemptCardHTML(a) {
  if (!a) return '';
  const tok = a.tokens || {}, tab = AW.attemptTab(a);
  const alerts = (a.notes || []).map(nt => `<div class="alert">${I.warn}<span>${esc(AW.noteText(nt, a.provider))}${nt.error ? ' — ' + esc(String(nt.error).slice(0, 160)) : ''}</span></div>`).join('')
    + (a.status === 'skipped' ? `<div class="alert">${I.warn}<span>${esc(t('skipped_why', { e: a.error }))}</span></div>` : '')
    + (a.warning ? `<div class="alert pk">${I.warn}<span>${esc(a.warning)}</span></div>` : '')
    + (a.truncated ? `<div class="alert pk">${I.warn}<span>${esc(t('truncated_note', { n: a.max_tokens }))}</span></div>` : '');
  const p = AW.attemptPayload(a, tab);
  const label = { system: t('t_system'), input: t('input_to', { p: a.provider }), response: t('t_response'), error: t('t_error'), raw: t('raw_event') }[tab];
  return `<div class="acard${a.status === 'ok' ? '' : ' plain'}">
    <div class="acard-hdr"><div class="l"><span class="t-lbl-md c-sec" style="font-weight:700">${esc(a.provider)}/${esc(a.model || '')}</span>
        <span class="pill ${esc(a.status)}">${esc(U(AW.stLabel(a.status)))}</span>${a.finish_reason ? `<span class="t-lbl-sm c-out">${esc(t('finish', { r: a.finish_reason }))}</span>` : ''}</div>
      <div class="t-lbl-sm c-on">${esc(U(t('sw_latency')))}: <span class="c-sec" style="font-weight:700">${a.status === 'running' ? `<span data-since="${a.ts}">${dfmt(nowS() - a.ts)}</span>` : esc(dfmt(a.dur))}</span>
        · ${esc(U(t('col_tokens')))}: <span class="c-mint" style="font-weight:700">${esc(t('sw_tok_pg', { i: n(tok.in || 0), o: n(tok.out || 0) }))}</span> · ${esc(t('max_n', { n: n(a.max_tokens) }))}</div></div>
    ${alerts}
    <div class="subtabs">${AW.attemptTabs(a).map(([k, l]) => `<button data-act="ptab" data-att-id="${a.id}" data-tab="${k}" class="${k === tab ? 'on' : ''}">${esc(l)}</button>`).join('')}</div>
    <div class="term-wrap">${p ? termHTML(label, p) : `<div class="empty" style="padding:16px">${esc(t('no_system'))}</div>`}</div></div>`;
}

function inspectHTML() {
  const d = S.detail;
  PAY = [];
  if (!d) return `<div class="empty"><b>${esc(S.detailMissing ? t('not_found') : t('select_title'))}</b>${esc(S.detailMissing ? t('not_found_txt') : t('select_txt'))}</div>`;
  const s = d.summary, sess = d.session || {}, req = d.args || {};
  const keys = ['task', 'input', 'prompt', 'system', 'items'], params = Object.entries(req).filter(([k]) => !keys.includes(k)), pk = keys.find(k => req[k]);
  const dl = params.length ? `<dl>${params.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(Array.isArray(v) ? v.join(', ') : typeof v === 'string' ? v : JSON.stringify(v))}</dd>`).join('')}</dl>` : '';
  const pctDone = s.jobs ? Math.round(s.jobs_done / s.jobs * 100) : null;
  const kbox = (k, v, sub, cls) => `<div class="kbox"><span class="t-lbl-sm c-out up">${esc(U(k))}</span><span class="v ${cls}">${v}</span><span class="t-lbl-sm c-out">${esc(sub)}</span></div>`;
  return `<div class="ribbon"><div class="l"><span class="dot d12 ${dotFor(d.status)}" style="${d.status === 'ok' ? 'box-shadow:0 0 8px #05ffa1' : ''}"></span>
      <div style="min-width:0"><div style="display:flex;align-items:center;gap:8px;min-width:0"><h1 class="c-on">${esc(d.title || d.tool)}</h1><span class="pill ${esc(d.status)}">${esc(U(AW.stLabel(d.status)))}</span></div>
        <div class="t-lbl-sm c-out" style="margin-top:2px">${esc(U(t('sw_call_id')))}: <span class="c-on">${esc(d.id)}</span> · ${esc(U(t('sw_dispatched')))}: ${esc(new Date(d.ts * 1000).toLocaleString(AW.locale()))} (${esc(AW.ago(d.ts))}) · PID ${esc(sess.pid ?? '—')}</div></div></div>
      <button class="btn" data-act="copy-json">${I.copy}${esc(U(t('copy_json')))}</button></div>
    <div class="inspect-body">
      ${dagHTML(d)}
      <div class="kgrid">
        ${kbox(t('f_duration'), d.status === 'running' ? `<span data-since="${d.ts}">${dfmt(nowS() - d.ts)}</span>` : esc(dfmt(d.dur)), d.status === 'running' ? t('s_running') : AW.stLabel(d.status), 'c-sec glow-cyan')}
        ${kbox(t('sw_from_agent'), esc(U(AW.who(sess))), sess.client || '—', 'c-ter')}
        ${kbox(t('f_project'), esc(s.project || '—'), `pid ${sess.pid ?? '—'}`, 'c-on')}
        ${kbox(t('sw_jobs_exec'), s.jobs ? `${s.jobs_done}/${s.jobs}` : '—', pctDone === null ? '—' : t('sw_completed', { p: pctDone }), pctDone === 100 ? 'c-mint' : 'c-sec')}
        ${kbox(t('f_attempts'), esc(U(AW.tp('sw_attempts_n', s.attempts))), [s.failed_attempts ? t('failed_n', { n: s.failed_attempts }) : null, s.skipped_attempts ? t('skipped_n', { n: s.skipped_attempts }) : null].filter(Boolean).join(' · ') || t('sw_no_retries'), s.failed_attempts || s.skipped_attempts ? 'c-orange' : 'c-on')}
        ${kbox(t('col_tokens'), s.tokens.in || s.tokens.out ? `${n(s.tokens.in)} → ${n(s.tokens.out)}` : '—', t('sw_in_out'), 'c-sec')}
      </div>
      ${d.status === 'abandoned' ? `<div class="note-strip pk">${esc(t('abandoned_txt', { pid: sess.pid }))}</div>` : ''}
      ${waterfallHTML(d)}
      ${attemptCardHTML(AW.currentAttempt())}
      <div class="acard plain"><div class="acard-hdr"><span class="t-lbl-md c-ter" style="font-weight:700">${esc(U(t('request_from', { w: AW.who(sess) })))}</span></div>
        <div class="term-wrap">${pk ? termHTML(pk, req[pk], dl) : `<div class="term"><div class="term-body">${dl || esc(t('no_args'))}</div></div>`}</div></div>
      <div class="acard plain"><div class="acard-hdr"><span class="t-lbl-md c-orange" style="font-weight:700">${esc(U(sess.source === 'cli' ? t('returned_cli') : t('returned_claude')))}</span></div>
        ${d.error ? `<div class="alert pk">${I.warn}<span>${esc(d.error)}</span></div>` : ''}
        <div class="term-wrap">${d.result ? termHTML(t('result'), d.result) : `<div class="empty" style="padding:16px">${esc(d.status === 'running' ? t('still_running') : t('no_result'))}</div>`}</div></div>
    </div>`;
}

function activityHTML() {
  return `<div data-role="ticker">${tickerHTML()}</div>
    <main class="dock"><aside class="feed" data-role="feed">${feedHTML()}</aside><section class="inspect" data-role="inspect">${inspectHTML()}</section></main>`;
}

/* both tabs share one shell: the top bar is outside the scrolling area, so no scrollbar can shift it */
function shellHTML() {
  return `<div class="sw-bg-grid"></div><div class="sw-bg-fade"></div><div class="sw-scan"></div>
    <div class="sw-app" data-page="${S.tab}">${topHTML()}
      ${S.tab === 'overview' ? `<div class="sw-scroll" data-role="scroll">${overviewHTML()}</div>` : `<div class="sw-page">${activityHTML()}</div>`}</div>`;
}

/* ================================================================ render + events */
function render(what) {
  const app = $('.sw-app');
  if (what === 'all' || what === 'tab' || !app || app.dataset.page !== S.tab) { root.innerHTML = shellHTML(); return; }
  $('.sw-top').outerHTML = topHTML();
  if (S.tab === 'overview') {
    if (what === 'usage' || what === 'calls' || what === 'live') {
      const sc = $('[data-role="scroll"]'), y = sc.scrollTop;
      sc.innerHTML = overviewHTML();
      sc.scrollTop = y;
    }
    return;
  }
  if (what === 'calls' || what === 'live') {
    const rows = $('[data-role="rows"]'), keep = rows ? rows.scrollTop : 0;
    const focused = document.activeElement && document.activeElement.dataset.role === 'search', caret = focused ? document.activeElement.selectionStart : null;
    $('[data-role="feed"]').innerHTML = feedHTML();
    $('[data-role="rows"]').scrollTop = keep;
    if (focused) { const q = $('[data-role="search"]'); q.focus(); q.setSelectionRange(caret, caret); }
    $('[data-role="ticker"]').innerHTML = tickerHTML();
  }
  if (what === 'detail') {
    const box = $('[data-role="inspect"]'), keep = box.scrollTop;
    box.innerHTML = inspectHTML();
    box.scrollTop = keep;
  }
}

AW.views.synthwave = {
  mount(r) {
    root = r;
    AW.bindControls(root);
    root.addEventListener('click', async e => {
      const tab = e.target.closest('[data-tab]'); if (tab) return AW.setTab(tab.dataset.tab);
      const att = e.target.closest('[data-att]'); if (att) return AW.selectAttempt(att.dataset.att);
      const call = e.target.closest('[data-call]'); if (call) return AW.select(call.dataset.call);
      const el = e.target.closest('[data-act]'); if (!el) return;
      const act = el.dataset.act;
      if (act === 'refresh') AW.refreshUsage();
      else if (act === 'auto') AW.setAuto(!S.auto);
      else if (act === 'table') AW.toggleTable();
      else if (act === 'pause') AW.setPaused(!S.paused);
      else if (act === 'resume') AW.setPaused(false);
      else if (act === 'ptab') AW.setPtab(el.dataset.attId, el.dataset.tab);
      else if (act === 'wrap') AW.toggleWrap();
      else if (act === 'copy') AW.copyPayload(PAY[+el.dataset.p]);
      else if (act === 'copy-json') AW.copy(JSON.stringify(S.detail, null, 2));
      else if (act === 'full') { el.disabled = true; el.textContent = t('loading_btn');
        try { await AW.loadBlob(el.dataset.ref); } catch (err) { el.textContent = t('unavailable'); } }
    });
    root.addEventListener('input', e => {
      if (e.target.dataset.role === 'search') AW.setFilter('q', e.target.value);
      else if (e.target.dataset.filter) AW.setFilter(e.target.dataset.filter, e.target.value);
    });
  },
  unmount() { root = null; },
  update(what) {
    if (!root) return;
    if (what === 'tick') { for (const el of root.querySelectorAll('[data-tplus]')) el.textContent = fmtTplus(nowS() - +el.dataset.tplus); return; }
    render(what);
  },
};
})();
