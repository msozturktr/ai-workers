/* Modern theme: clean light/dark dashboard. */
(() => {
'use strict';
const { t, tp, n, esc, kfmt, dfmt, nowS } = AW;
const S = AW.state;
let root;
const $ = s => root.querySelector(s);

const COLORS = { none: 'var(--muted)', good: 'var(--good)', warning: 'var(--warning)', serious: 'var(--serious)', critical: 'var(--critical)' };
const ICONS = { none: '○', good: '●', warning: '◆', serious: '▲', critical: '■' };

function shell() {
  return `
  <div class="app">
    <header class="topbar">
      <div class="brand" lang="en"><img src="/icon.svg" alt="">ai-workers</div>
      <nav class="tabs" role="tablist">
        <button role="tab" data-tab="overview"><span data-t="tab_overview"></span></button>
        <button role="tab" data-tab="activity"><span data-t="tab_activity"></span> <span class="badge" data-role="runbadge" hidden>0</span></button>
      </nav>
      <div class="spacer"></div>
      <span class="livestate" data-role="live"><i></i><span></span></span>
      <div class="controls">${AW.controlsHTML()}<button data-role="mode" aria-label="Light / dark">◐</button></div>
    </header>
    <main>
      <section class="tab" data-page="overview">
        <div class="wrap">
          <div class="bar">
            <button data-act="refresh" data-t="refresh"></button>
            <button data-act="auto"></button>
            <button data-act="table" data-t="table_view"></button>
            <span class="sub" data-role="asof"></span>
          </div>
          <div class="card hero-card">
            <div><div class="hero-num" data-role="hero">—</div><div class="hero-lab" data-role="hero-lab"></div></div>
            <div class="kpis" data-role="kpis"></div>
          </div>
          <h2 data-t="h_workers"></h2>
          <div class="card" data-role="workers"></div>
          <h2 data-t="h_claude"></h2>
          <div class="card" data-role="claude"></div>
          <div data-role="tablewrap" hidden><h2 data-t="table_view"></h2><div class="card scroll"><table data-role="table"></table></div></div>
          <footer data-role="foot"></footer>
        </div>
      </section>
      <section class="tab" data-page="activity">
        <div class="now" data-role="now"></div>
        <div class="split">
          <aside class="list-pane">
            <div class="filters">
              <input data-role="search" type="search" autocomplete="off" spellcheck="false">
              <select data-filter="status"></select><select data-filter="tool"></select><select data-filter="prov"></select>
              <button data-act="pause"></button>
            </div>
            <div class="list" data-role="calls" tabindex="0"></div>
            <div class="list-foot"><span data-role="count"></span><span data-role="retention"></span></div>
          </aside>
          <section class="detail-pane" data-role="detail"></section>
        </div>
      </section>
    </main>
  </div>`;
}

/* ---------------------------------------------------------------- overview */
function meterRow(name, sub, remaining, limit, pct, reset, source) {
  const st = AW.statusOf(pct), col = COLORS[st.key];
  return `<div class="row">
    <div class="row-name">${esc(name)}<small>${esc(sub || '')}</small></div>
    <div class="meter">
      <div class="track"><div class="fill" style="width:${pct ?? 0}%;background:${col}"></div></div>
      <div class="meter-cap"><span>${esc(source || '')}</span><span>${reset ? esc(t('reset_cap', { t: reset })) : ''}</span></div>
    </div>
    <div class="status"><span class="dot" style="background:${col}"></span>${remaining === undefined ? `<b>${esc(t('pct_left', { p: pct === null ? '—' : pct.toFixed(0) }))}</b>` : `<b>${n(remaining)}</b> / ${n(limit)}`}
      <div style="color:var(--muted);font-size:11.5px">${ICONS[st.key]} ${esc(st.label)}${pct !== null && remaining !== undefined ? ' · ' + pct.toFixed(0) + '%' : ''}</div></div>
  </div>`;
}

function renderUsage() {
  const m = S.model;
  $('[data-act="auto"]').textContent = S.auto ? t('auto_on') : t('auto_off');
  $('[data-role="tablewrap"]').hidden = !S.showTable;
  if (!m) { $('[data-role="asof"]').textContent = S.usageError ? t('error_prefix', { e: S.usageError }) : t('loading'); return; }
  $('[data-role="asof"]').textContent = S.usageError ? t('error_prefix', { e: S.usageError })
    : t('as_of', { t: new Date(m.generated * 1000).toLocaleTimeString(AW.locale()) });

  let rows = '';
  for (const x of m.meters) {
    if (x.kind === 'nokey') rows += `<div class="row"><div class="row-name">${esc(x.provider)}<small>${t('no_key')}</small></div>
      <div class="meter"><div class="meter-cap"><span>${t('unconfigured')}</span></div></div><div class="status" style="color:var(--muted)">—</div></div>`;
    else if (x.kind === 'group') rows += `<div class="row"><div class="row-name">${esc(x.provider)}<small>${esc(x.tier || t('per_model_limit'))}</small></div>
      <div class="meter"><div class="meter-cap"><span>${esc(x.source)}</span><span>${t('req_today', { n: x.requests })}</span></div></div>
      <div class="status" style="color:var(--muted);font-size:11.5px">${t('models_n', { n: x.models })}</div></div>`;
    else if (x.kind === 'model') {
      const cap = [x.minReq ? t('min_req', { u: n(x.minReq.used), l: n(x.minReq.limit) }) : null,
                   x.minTok ? t('min_tok', { u: n(x.minTok.used), l: n(x.minTok.limit) }) : null,
                   x.free ? t('free_left', { r: n(x.free.remaining), l: n(x.free.limit) }) : null].filter(Boolean).join(' · ');
      rows += meterRow('↳ ' + x.short, t('req_today', { n: x.requests }), x.remaining, x.limit, x.pct, x.reset, cap);
    } else if (x.kind === 'provider') rows += meterRow(x.name, t('req_today', { n: x.requests }), x.remaining, x.limit, x.pct, x.reset, x.source);
    else if (x.kind === 'window') rows += meterRow('', x.unit === 'tok_min' ? (x.name.includes('·') ? `${x.name.split('·').pop().trim()} · ${t('token_window')}` : t('token_window')) : t('per_min_requests'), x.remaining, x.limit, x.pct, x.reset, x.source);
    else if (x.kind === 'unlimited') rows += `<div class="row"><div class="row-name">${esc(x.provider)}<small>${t('req_today', { n: x.requests })}</small></div>
      <div class="meter"><div class="meter-cap"><span>${esc(x.source)}</span></div></div>
      <div class="status"><b>${t('requests_n', { n: n(x.requests) })}</b><div style="color:var(--muted);font-size:11.5px">○ ${t('limit_undefined')}</div></div></div>`;
  }
  if (m.notes.length) rows += `<div class="warnbox">${m.notes.map(x => '⚠ ' + esc(x)).join('<br>')}</div>`;
  $('[data-role="workers"]').innerHTML = rows;

  const tg = m.tightest;
  $('[data-role="hero"]').textContent = tg ? tg.pct.toFixed(0) + '%' : '—';
  $('[data-role="hero"]').style.color = tg ? COLORS[AW.statusOf(tg.pct).key] : '';
  $('[data-role="hero-lab"]').textContent = !tg ? t('no_live_quota') : tg.remaining === undefined
    ? t('tightest_of', { p: tg.label }) + (tg.resets ? ' · ' + t('resets_at', { t: tg.resets }) : '')
    : t('tightest_left', { p: tg.label, r: n(tg.remaining), l: n(tg.limit) });
  $('[data-role="kpis"]').innerHTML = `
    <div><div class="kpi-v">${n(m.kpis.req)}</div><div class="kpi-l">${t('kpi_req_today')}</div></div>
    <div><div class="kpi-v">${n(m.kpis.tok)}</div><div class="kpi-l">${t('kpi_tok_today')}</div></div>
    <div><div class="kpi-v" style="${m.kpis.fail ? 'color:var(--critical)' : ''}">${n(m.kpis.fail)}</div><div class="kpi-l">${t('kpi_failed')}</div></div>`
    + (m.efficiency ? `<div class="kpi-l" style="flex-basis:100%;margin-top:-10px;text-transform:none">${esc(AW.effText(m.efficiency).share)} · ${esc(AW.effText(m.efficiency).cache)}</div>` : '');

  const c = m.claude;
  let ch = '';
  if (c.pending) ch += `<div class="note">${esc(t('live_pending'))}</div>`;
  else if (c.limits.length) {
    const src = (c.age < 60 ? t('live_now') : t('live_ago', { n: Math.round(c.age / 60) })) + (c.refreshing ? t('refreshing') : '');
    for (const L of c.limits) ch += meterRow(L.label, t('pct_used', { p: L.used }), undefined, undefined, L.pct, L.resets, src);
  } else if (c.error) ch += `<div class="warnbox">⚠ ${esc(t('live_error', { e: c.error }))}</div>`;
  ch += `<div class="kpis">
      <div><div class="kpi-v">${n(c.calls)}</div><div class="kpi-l">${t('kpi_req_today')}</div></div>
      <div><div class="kpi-v">${n(c.out)}</div><div class="kpi-l">${t('kpi_out_tok')}</div></div>
      <div><div class="kpi-v">${n(c.cacheRead)}</div><div class="kpi-l">${t('kpi_cache')}</div></div>
      <div><div class="kpi-v">${n(c.sessions)}</div><div class="kpi-l">${t('kpi_sessions')}</div></div></div>`;
  if (c.lastEvent) {
    const reset = new Date(c.lastEvent.resetsAt * 1000);
    ch += `<div class="warnbox">${t('last_event', { type: esc(c.lastEvent.rateLimitType || '?'), status: esc(c.lastEvent.status || '?'), t: reset.toLocaleString(AW.locale()) })}
      ${reset < new Date() ? `<span class="stale">${t('past_event')}</span>` : ''}</div>`;
  }
  const spans = c.spans.map(s => t('span_req', { s: AW.bx(s.span), n: n(s.requests) }) + (s.sessions ? t('span_sess', { n: s.sessions }) : '')).join('  ·  ');
  if (spans) ch += `<div class="note">${esc(t('anthropic_count', { s: spans }))}</div>`;
  $('[data-role="claude"]').innerHTML = ch;

  if (S.showTable) {
    const rowsT = m.workers.map(w => {
      const rq = (w.quota && w.quota.requests) || {};
      let tr = `<tr><td>${esc(w.provider)}</td><td>${w.ready ? t('ready') : t('no_key')}</td><td>${n(w.today_requests)}</td>
        <td>${n(w.today_tokens_in)}</td><td>${n(w.today_tokens_out)}</td><td>${n(rq.remaining)}</td><td>${n(rq.limit)}</td><td>${esc(AW.bx(w.source))}</td></tr>`;
      for (const md of (w.models || [])) {
        const dq = (md.quota || {}).day_requests || {};
        tr += `<tr><td>&nbsp;&nbsp;↳ ${esc(md.model)}</td><td>${t('local_counter')}</td><td>${n(md.requests_today)}</td>
          <td colspan="2">${n(md.tokens_today)} ${t('tok')}</td><td>${n(dq.remaining)}</td><td>${n(dq.limit)}</td><td>${t('daily_requests')}</td></tr>`;
      }
      return tr;
    }).join('') + c.limits.map(L => `<tr><td>Claude · ${esc(L.label)}</td><td>${t('live')}</td><td>—</td><td>—</td><td>—</td>
      <td>${L.pct}%</td><td>100%</td><td>claude -p /usage</td></tr>`).join('');
    $('[data-role="table"]').innerHTML = `<thead><tr><th>${t('th_provider')}</th><th>${t('th_status')}</th><th>${t('th_requests')}</th>
      <th>${t('th_in')}</th><th>${t('th_out')}</th><th>${t('th_remaining')}</th><th>${t('th_limit')}</th><th>${t('th_source')}</th></tr></thead><tbody>${rowsT}</tbody>`;
  }
  $('[data-role="foot"]').textContent = t('footer');
}

/* ---------------------------------------------------------------- activity */
function renderNow() {
  const run = AW.running();
  $('[data-role="now"]').innerHTML = `<span class="now-label">${t('now_running')}</span>` + (!run.length
    ? `<span class="now-empty">${t('idle')}</span>`
    : run.map(c => `<span class="chip" data-call="${c.id}" title="${esc(c.title || '')}">
      <span class="st running"></span><b>${esc(c.tool)} · ${esc(c.title || '')}</b>
      ${c.jobs > 1 ? `<span class="num">${c.jobs_done}/${c.jobs}</span>` : ''}
      ${c.active && c.active.length ? `<span>${esc([...new Set(c.active)].slice(0, 2).join(', '))}</span>` : ''}
      <span class="num" data-since="${c.ts}">${dfmt(nowS() - c.ts)}</span></span>`).join(''));
}

function rowHTML(c) {
  const bits = [`<span>${esc(AW.who(c))}${c.project ? ' · ' + esc(c.project) : ''}</span>`];
  if (c.jobs > 1) bits.push(`<span>${t('jobs_x', { d: c.jobs_done, t: c.jobs })}</span>`);
  if (c.providers.length) bits.push(`<span>${esc(c.providers.slice(0, 2).join(', '))}${c.providers.length > 2 ? ' +' + (c.providers.length - 2) : ''}</span>`);
  if (c.tokens.in || c.tokens.out) bits.push(`<span>${kfmt(c.tokens.in)} → ${kfmt(c.tokens.out)} ${t('tok')}</span>`);
  bits.push(c.status === 'running' ? `<span data-since="${c.ts}">${dfmt(nowS() - c.ts)}</span>` : `<span>${dfmt(c.dur)}</span>`);
  if (c.failed_attempts) bits.push(`<span style="color:var(--serious)">${tp('failed_att', c.failed_attempts)}</span>`);
  if (c.skipped_attempts) bits.push(`<span title="${esc(t('skipped_title'))}">${t('skipped_n', { n: c.skipped_attempts })}</span>`);
  return `<div class="crow${c.id === S.sel ? ' sel' : ''}" data-call="${c.id}" role="option" aria-selected="${c.id === S.sel}">
    <span class="st ${esc(c.status)}" title="${esc(AW.stLabel(c.status))}"></span>
    <div class="t"><span class="tool" lang="en">${esc(c.tool)}</span>${esc(c.title || '')}</div>
    <span class="time" title="${esc(new Date(c.ts * 1000).toLocaleString(AW.locale()))}">${AW.dtfmt(c.ts)}</span>
    <div class="meta">${bits.join('')}</div></div>`;
}

function renderCalls() {
  const b = $('[data-role="runbadge"]'), running = AW.running().length;
  b.hidden = !running; b.textContent = running;
  document.title = (running ? `(${running}) ` : '') + 'ai-workers';
  const opts = AW.filterOptions();
  for (const key of ['status', 'tool', 'prov']) {
    const sel = $(`[data-filter="${key}"]`);
    const html = opts[key].map(v => `<option value="${esc(v)}"${v === S.filters[key] ? ' selected' : ''}>${esc(AW.filterLabel(key, v))}</option>`).join('');
    if (sel.dataset.sig !== html) { sel.innerHTML = html; sel.dataset.sig = html; }
  }
  const pause = $('[data-act="pause"]');
  pause.textContent = S.paused ? t('paused') : t('pause'); pause.classList.toggle('on', S.paused);
  pause.title = t('pause_title');
  const host = $('[data-role="calls"]'), keep = host.scrollTop, rows = AW.filtered(), fresh = AW.freshCount();
  let html = fresh ? `<div class="newbar" data-act="resume">${AW.tp('new_calls', fresh)}</div>` : '';
  if (!S.shown.length) html += `<div class="empty"><b>${t('empty_title')}</b>${t('empty_txt')}</div>`;
  else if (!rows.length) html += `<div class="empty"><b>${t('nomatch_title')}</b>${t('nomatch_txt')}</div>`;
  else html += rows.map(rowHTML).join('');
  host.innerHTML = html; host.scrollTop = keep;
  $('[data-role="count"]').textContent = S.shown.length
    ? (rows.length === S.shown.length ? t('calls_n', { n: S.shown.length }) : t('calls_of', { a: rows.length, b: S.shown.length })) : '';
  $('[data-role="retention"]').textContent = S.settings
    ? (S.settings.enabled ? t('kept', { d: S.settings.retention_days, mb: S.settings.max_mb }) : t('logging_off')) : '';
  renderNow();
}

function payloadHTML(label, p) {
  if (!p) return '';
  const x = AW.payloadText(p), key = 'p' + (PAY.push(p) - 1);
  return `<div class="payload">
    <div class="pl-h"><span class="lbl">${esc(label)}</span><span class="num">${t('chars_n', { n: n(x.size) })}</span><span class="sp"></span>
      <button data-act="wrap" title="${esc(t('wrap_title'))}">${S.wrap ? t('nowrap') : t('wrap')}</button>
      <button data-act="copy" data-p="${key}">${t('copy')}</button></div>
    <pre class="code${S.wrap ? '' : ' nowrap'}">${!x.text && !x.partial ? `<span style="color:var(--muted)">${t('empty')}</span>` : esc(x.text)}</pre>
    ${x.partial ? `<div class="more">${t('showing_first', { a: n(x.text.length), b: n(x.size) })}<button data-act="full" data-ref="${x.ref}">${t('load_full')}</button></div>` : ''}
  </div>`;
}
let PAY = [];

function flowHTML(d) {
  const sess = d.session || {}, jobs = AW.jobs(d).length, arrow = '<span class="farrow">→</span>';
  const st = AW.stations(d);
  const provs = st.length ? arrow + '<div class="fstack">' + st.map(g => {
    const parts = [g.ok && t('ok_n', { n: g.ok }), g.error && t('failed_n', { n: g.error }), g.skipped && t('skipped_n', { n: g.skipped }), g.running && t('running_n', { n: g.running })].filter(Boolean);
    return `<div class="fnode ${g.status === 'running' ? '' : g.status}">${esc(g.provider)}<small>${esc(g.model || '')} · ${parts.join(', ')}${g.tin ? ` · ${kfmt(g.tin)}→${kfmt(g.tout)} ${t('tok')}` : ''}</small></div>`;
  }).join('') + '</div>' : '';
  const back = d.status === 'running' ? `<div class="fnode">${t('in_progress')}<small>${t('result_pending')}</small></div>`
    : `<div class="fnode ${d.status === 'ok' ? 'ok' : ['error', 'abandoned'].includes(d.status) ? 'error' : ''}">${esc(t('result_to', { w: AW.who(sess) }))}<small>${d.result ? t('chars_n', { n: n(d.result.size) }) : esc(AW.stLabel(d.status))}</small></div>`;
  return `<div class="flow"><div class="fnode">${esc(AW.who(sess))}<small>${esc([sess.client, sess.cwd ? sess.cwd.split('/').pop() : null].filter(Boolean).join(' · ') || '—')}</small></div>${arrow}
    <div class="fnode">ai-workers · ${esc(d.tool)}<small>${jobs ? AW.tp('jobs_n', jobs) : t('local')}</small></div>${provs}${arrow}${back}</div>`;
}

function attemptHTML(a) {
  const tok = a.tokens || {}, tab = AW.attemptTab(a);
  const rl = a.ratelimit ? Object.entries(a.ratelimit).filter(([, v]) => v != null).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(' · ') : '';
  const notes = (a.notes || []).map(nt => `<div>${esc(AW.noteText(nt, a.provider))}<br><span style="color:var(--muted)">${esc(String(nt.error || '').slice(0, 300))}</span></div>`).join('')
    + (a.status === 'skipped' ? `<div>${esc(t('skipped_why', { e: a.error }))}</div>` : '')
    + (a.warning ? `<div class="err">${esc(a.warning)}</div>` : '') + (a.truncated ? `<div class="err">${t('truncated_note', { n: a.max_tokens })}</div>` : '');
  const p = AW.attemptPayload(a, tab);
  const label = { system: t('t_system'), input: t('input_to', { p: a.provider }), response: t('t_response'), error: t('t_error'), raw: t('raw_event') }[tab];
  return `<div class="att">
    <div class="att-h"><span class="st ${esc(a.status)}"></span><b>${esc(a.provider)}/${esc(a.model || '')}</b><span class="pill">${esc(AW.stLabel(a.status))}</span>
      ${a.tries > 1 ? `<span>${t('tries_n', { n: a.tries })}</span>` : ''}${a.finish_reason ? `<span style="color:var(--muted)">${esc(t('finish', { r: a.finish_reason }))}</span>` : ''}
      <span class="r">${a.status === 'running' ? `<span data-since="${a.ts}">${dfmt(nowS() - a.ts)}</span>` : dfmt(a.dur)}${tok.in || tok.out ? ` · ${n(tok.in)} → ${n(tok.out)} ${t('tok')}` : ''} · ${t('max_n', { n: n(a.max_tokens) })}</span></div>
    ${notes ? `<div class="att-notes">${notes}</div>` : ''}${rl ? `<div class="att-notes" style="color:var(--muted)">${esc(t('rate_limit', { s: rl }))}</div>` : ''}
    <div class="ptabs">${AW.attemptTabs(a).map(([k, l]) => `<button data-act="ptab" data-att="${a.id}" data-tab="${k}" aria-selected="${k === tab}">${l}</button>`).join('')}</div>
    ${p ? payloadHTML(label, p) : `<div class="more">${t('no_system')}</div>`}
  </div>`;
}

const sameBlob = (a, b) => !!a && !!b && (a.ref ? a.ref === b.ref : a.text === b.text);
function waterfallHTML(d) {
  const jobs = AW.jobs(d);
  if (!jobs.length) return '';
  const head = `<div class="wrow" style="cursor:default;background:none;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em">
    <span></span><span>${t('col_job')}</span><span>${t('col_model')}</span><span class="lane-c">${t('col_timeline', { d: dfmt((d.end || nowS()) - d.ts) })}</span><span class="r">${t('col_time')}</span><span class="r tok">${t('col_tokens')}</span></div>`;
  return `<h3>${t('h_jobs', { n: jobs.length })}</h3><div class="wf">${head}${jobs.map(j => {
    const atts = AW.attempts(j), used = atts.filter(a => a.status === 'ok').pop() || atts[atts.length - 1];
    const pm = j.provider ? `${j.provider}/${j.model || ''}` : used ? `${used.provider}/${used.model || ''}` : '—';
    const tin = atts.reduce((s, a) => s + ((a.tokens || {}).in || 0), 0), tout = atts.reduce((s, a) => s + ((a.tokens || {}).out || 0), 0);
    const segs = (atts.length ? atts : [j]).map(a => `<div class="seg-b ${esc(a.status)}" data-s="${a.ts}" data-e="${a.end || ''}" title="${esc((a.provider ? a.provider + '/' + (a.model || '') + ': ' : '') + AW.stLabel(a.status) + ' · ' + dfmt(a.dur ?? nowS() - a.ts))}"></div>`).join('');
    const open = S.open.has(j.id);
    return `<div class="wrow${open ? ' open' : ''}" data-act="toggle" data-id="${j.id}" title="${esc(t('job_click'))}">
      <span class="st ${esc(j.status)}"></span><span class="lab">${esc(j.label || t('job_word'))}</span>
      <span class="pm">${esc(j.role || '—')} → ${esc(pm)}${j.fallback ? ` <span style="color:var(--serious)">${t('fallback_mark')}</span>` : ''}</span>
      <span class="lane-c"><div class="lane">${segs}</div></span>
      <span class="r">${j.status === 'running' ? `<span data-since="${j.ts}">${dfmt(nowS() - j.ts)}</span>` : dfmt(j.dur)}</span>
      <span class="r tok">${tin || tout ? `${kfmt(tin)} → ${kfmt(tout)}` : '—'}</span></div>
      ${open ? `<div class="wdetail">${j.error ? `<div class="att-notes"><div class="err">${esc(j.error)}</div></div>` : ''}
        ${(j.notes || []).length ? `<div class="att-notes">${j.notes.map(nt => `<div>${esc(AW.noteText(nt))}</div>`).join('')}</div>` : ''}
        ${j.task && !sameBlob(j.task, (d.args || {}).task) ? payloadHTML(t('task'), j.task) : ''}
        ${atts.length ? atts.map(attemptHTML).join('') : `<div class="more">${t('no_request')}</div>`}</div>` : ''}`;
  }).join('')}</div>`;
}

function layoutLanes() {
  const d = S.detail; if (!d) return;
  const t0 = d.ts, span = Math.max((d.end || nowS()) - t0, 0.001);
  for (const seg of root.querySelectorAll('.seg-b')) {
    const s = +seg.dataset.s, e = seg.dataset.e ? +seg.dataset.e : nowS();
    seg.style.left = Math.max(0, (s - t0) / span * 100) + '%';
    seg.style.width = Math.max(0.6, (e - s) / span * 100) + '%';
  }
}

const PAYLOAD_KEYS = ['task', 'input', 'prompt', 'system', 'items'];
function renderDetail() {
  const pane = $('[data-role="detail"]'), d = S.detail;
  PAY = [];
  if (!d) {
    pane.innerHTML = S.detailMissing ? `<div class="empty"><b>${t('not_found')}</b>${t('not_found_txt')}</div>`
      : `<div class="empty"><b>${t('select_title')}</b>${t('select_txt')}</div>`;
    return;
  }
  const keep = pane.scrollTop, s = d.summary, sess = d.session || {}, running = d.status === 'running', req = d.args || {};
  const facts = [
    [t('f_duration'), running ? `<span data-since="${d.ts}">${dfmt(nowS() - d.ts)}</span>` : dfmt(d.dur)],
    [t('f_from'), esc(AW.who(sess)) + (sess.client ? `<div class="fact-l" style="text-transform:none;letter-spacing:0">${esc(sess.client)}</div>` : '')],
    [t('f_project'), esc(s.project || '—')],
    [t('f_jobs'), s.jobs ? `${s.jobs_done}/${s.jobs}` : '—'],
    [t('f_attempts'), s.attempts ? `${s.attempts}${s.failed_attempts ? ` <span style="color:var(--serious);font-weight:500">${t('n_failed_p', { n: s.failed_attempts })}</span>` : ''}${s.skipped_attempts ? ` <span style="color:var(--muted);font-weight:500">${t('n_skipped_p', { n: s.skipped_attempts })}</span>` : ''}` : '—'],
    [t('f_tokens'), s.tokens.in || s.tokens.out ? `${n(s.tokens.in)} → ${n(s.tokens.out)}` : '—'],
  ];
  const params = Object.entries(req).filter(([k]) => !PAYLOAD_KEYS.includes(k));
  pane.innerHTML = `
    <div class="d-head"><div><div class="d-title">${esc(d.title || d.tool)}</div>
      <div class="d-sub">${esc(d.tool)} · ${esc(new Date(d.ts * 1000).toLocaleString(AW.locale()))} (${AW.ago(d.ts)}) · pid ${esc(sess.pid ?? '—')} · <span class="mono">${esc(d.id)}</span></div></div>
      <div class="d-actions"><span class="pill"><span class="st ${esc(d.status)}"></span>${esc(AW.stLabel(d.status))}</span>
        <button data-act="copy-json" title="${esc(t('copy_json_title'))}">${t('copy_json')}</button></div></div>
    ${flowHTML(d)}
    <div class="facts">${facts.map(([l, v]) => `<div><div class="fact-v">${v}</div><div class="fact-l">${l}</div></div>`).join('')}</div>
    ${d.status === 'abandoned' ? `<div class="warnbox">${esc(t('abandoned_txt', { pid: sess.pid }))}</div>` : ''}
    <h3>${esc(t('request_from', { w: AW.who(sess) }))}</h3>
    ${params.length ? '<dl class="params">' + params.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${Array.isArray(v) ? v.map(x => esc(typeof x === 'string' ? x : JSON.stringify(x))).join('<br>') : esc(typeof v === 'string' ? v : JSON.stringify(v))}</dd>`).join('') + '</dl>' : ''}
    ${PAYLOAD_KEYS.filter(k => req[k]).map(k => payloadHTML(k === 'items' ? t('items_n', { n: req.item_count ?? '?' }) : k, req[k])).join('') || (Object.keys(req).length ? '' : `<div class="more">${t('no_args')}</div>`)}
    ${waterfallHTML(d)}
    <h3>${sess.source === 'cli' ? t('returned_cli') : t('returned_claude')}</h3>
    ${d.error ? `<div class="att-notes"><div class="err">${esc(d.error)}</div></div>` : ''}
    ${d.result ? payloadHTML(t('result'), d.result) : `<div class="more">${running ? t('still_running') : t('no_result')}</div>`}`;
  layoutLanes();
  pane.scrollTop = keep;
}

/* ---------------------------------------------------------------- wiring */
function renderStatic() {
  for (const el of root.querySelectorAll('[data-t]')) el.textContent = t(el.dataset.t);
  $('[data-role="search"]').placeholder = t('search_ph');
  $('[data-role="mode"]').title = t('mode_title');
  root.querySelector('.controls').firstElementChild.outerHTML = AW.controlsHTML();
  renderTab(); renderLive();
}
function renderTab() {
  for (const b of root.querySelectorAll('[data-tab]')) b.setAttribute('aria-selected', String(b.dataset.tab === S.tab));
  for (const p of root.querySelectorAll('[data-page]')) p.hidden = p.dataset.page !== S.tab;
}
function renderLive() {
  const el = $('[data-role="live"]');
  el.classList.toggle('on', S.live);
  el.title = t('live_title');
  el.querySelector('span').textContent = S.live ? t('live') : t('reconnecting');
}

AW.views.modern = {
  mount(r) {
    root = r;
    root.innerHTML = shell();
    AW.bindControls(root);
    root.addEventListener('click', async e => {
      const tab = e.target.closest('[data-tab]'); if (tab) return AW.setTab(tab.dataset.tab);
      if (e.target.closest('[data-role="mode"]')) return AW.toggleMode();
      const call = e.target.closest('[data-call]'); if (call) return AW.select(call.dataset.call);
      const el = e.target.closest('[data-act]'); if (!el) return;
      const act = el.dataset.act;
      if (act === 'refresh') AW.refreshUsage();
      else if (act === 'auto') AW.setAuto(!S.auto);
      else if (act === 'table') AW.toggleTable();
      else if (act === 'pause') AW.setPaused(!S.paused);
      else if (act === 'resume') AW.setPaused(false);
      else if (act === 'toggle') AW.toggleOpen(el.dataset.id);
      else if (act === 'ptab') AW.setPtab(el.dataset.att, el.dataset.tab);
      else if (act === 'wrap') AW.toggleWrap();
      else if (act === 'copy') AW.copyPayload(PAY[+el.dataset.p.slice(1)]);
      else if (act === 'copy-json') AW.copy(JSON.stringify(S.detail, null, 2));
      else if (act === 'full') { el.disabled = true; el.textContent = t('loading_btn');
        try { await AW.loadBlob(el.dataset.ref); } catch (err) { el.textContent = t('unavailable'); AW.toast(String(err.message || err).slice(0, 120)); } }
    });
    root.addEventListener('input', e => {
      if (e.target.dataset.role === 'search') AW.setFilter('q', e.target.value);
      else if (e.target.dataset.filter) AW.setFilter(e.target.dataset.filter, e.target.value);
    });
  },
  unmount() { root = null; },
  update(what) {
    if (!root) return;
    if (what === 'all') { renderStatic(); renderUsage(); renderCalls(); renderDetail(); $('[data-role="search"]').value = S.filters.q; }
    else if (what === 'usage') renderUsage();
    else if (what === 'calls') renderCalls();
    else if (what === 'detail') renderDetail();
    else if (what === 'tab') renderTab();
    else if (what === 'live') renderLive();
    else if (what === 'tick' && S.detail && S.detail.status === 'running') layoutLanes();
  },
};
})();
