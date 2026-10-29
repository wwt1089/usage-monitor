// 1920×480 키오스크. 서버가 SSE로 스냅샷을 밀어 주고, 화면은 그것만 그린다(브라우저 폴링 없음).
'use strict';

const $ = id => document.getElementById(id);
const SERIES = {
  cache: [['cache_read', '--s-cache-read'], ['cache_creation', '--s-cache-creation']],
  io: [['input', '--s-input'], ['output', '--s-output']],
};
const HEALTH_NAMES = { otel: 'OTel', statusline: 'statusline', get_usage: 'get_usage', appmeta: '앱 정보' };
const HEALTH_TEXT = { live: '실시간', recent: '30분 내', idle: '유휴', missing: '수집 끊김 의심' };
const HEARTBEAT_TIMEOUT_MS = 25000;
const SINCE_OBS_SHOW_MICROS = 100000;      // 관측 뒤 추정 비용이 $0.10 이상일 때만 표시한다
const SHIFT_EVERY_MS = 5 * 60 * 1000;
const SHIFTS = [[0, 0], [1, 0], [2, 1], [1, 2], [0, 1], [2, 2], [1, 1], [0, 2], [2, 0]];

let snap = null;
// 수신기에 auth_token을 설정했으면 화면 주소의 ?token= 을 SSE·확인 요청에도 붙인다(EventSource는 헤더를 못 붙인다)
const TOKEN = new URLSearchParams(location.search).get('token');
const TOKEN_QUERY = TOKEN ? '?token=' + encodeURIComponent(TOKEN) : '';
let lastMessageAt = 0;
let lastHeartbeatAt = Date.now();

// ── 형식 ───────────────────────────────────────────
const pad = n => String(n).padStart(2, '0');
function clock(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000), now = new Date();
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return d.toDateString() === now.toDateString() ? hm : `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
}
function span(sec) {
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  if (d) return `${d}일 ${h}시간`;
  if (h) return `${h}시간 ${m}분`;
  return `${m}분`;
}
function compact(n) {
  if (n == null) return '—';
  const trim = (v, d) => v.toFixed(d).replace(/\.0$/, '');     // 2.0M → 2M
  if (n >= 1e6) return trim(n / 1e6, n >= 1e7 ? 0 : 1) + 'M';
  if (n >= 1e3) return trim(n / 1e3, n >= 1e4 ? 0 : 1) + 'K';
  return String(n);
}
const usd = micros => '$' + (micros / 1e6).toFixed(2);
// claude-opus-5-5 → Opus 5.5, claude-haiku-4-5-20251001 → Haiku 4.5. 표시 이름(Opus 5)은 그대로 둔다
function modelName(m) {
  if (!m || !m.startsWith('claude-')) return m;
  const [, base, suffix = ''] = m.match(/^claude-(.+?)(\[.*\])?$/);
  const parts = base.replace(/-\d{8}$/, '').split('-');
  const family = parts.shift();
  return `${family[0].toUpperCase()}${family.slice(1)} ${parts.join('.')}${suffix ? ' ' + suffix : ''}`.trim();
}
const nowSec = () => Date.now() / 1000;

// ── 좌: 한도 ───────────────────────────────────────
function severity(pct) {
  return pct >= 90 ? 'critical' : pct >= 70 ? 'warning' : 'good';
}

function etaText(l) {
  switch (l.verdict) {
    case 'no_usage': return ['사용 없음', false];
    case 'safe': return ['리셋 전 소진 안 함', false];
    case 'exhaust_before_reset': return [`${clock(l.eta_ts)} 소진 예상`, true];
    case 'exhausted': return ['한도 도달', true];
    case 'reset': return ['리셋됨 · 새 관측 대기', false];
    default: return ['속도 산출 불가', false];
  }
}

function methodText(l) {
  if (l.verdict === 'reset') return `리셋 전 마지막 값 ${l.pct}%`;
  if (!l.method || l.rate_pct_per_hour == null) return '';
  const name = l.method === 'trend' ? '추세' : '창 평균';
  return `${name} · 시간당 ${l.rate_pct_per_hour.toFixed(1)}%p`;
}

function renderLimit(el, l) {
  const pctEl = el.querySelector('.pct');
  const bar = el.querySelector('.bar > div');
  if (!l) {
    pctEl.textContent = '—';
    el.querySelector('.eta-main').textContent = '';
    el.querySelector('.method').textContent = '';
    el.querySelector('.obs').textContent = '관측 없음';
    el.querySelector('.reset').textContent = '';
    bar.style.width = '0';
    return;
  }
  // 창이 리셋된 뒤에는 이전 값이 현재 값이 아니다. 숫자와 막대를 비우고 이전 값은 문구로만 남긴다
  const stale = l.verdict === 'reset';
  const html = stale ? '—' : `${l.pct}<small>%</small>`;
  if (pctEl.innerHTML !== html) {
    const first = pctEl.textContent === '—' || stale;   // 첫 로딩과 리셋 표시는 값이 바뀐 것이 아니므로 강조하지 않는다
    pctEl.innerHTML = html;
    if (!first) {
      pctEl.classList.add('flash');
      setTimeout(() => pctEl.classList.remove('flash'), 60);
    }
  }
  bar.className = 'sev-' + severity(l.pct);
  bar.style.width = stale ? '0' : Math.min(100, l.pct) + '%';
  const [main, alert] = etaText(l);
  const mainEl = el.querySelector('.eta-main');
  mainEl.innerHTML = (alert ? '<span class="icon-alert"></span>' : '') + main;
  mainEl.classList.toggle('alert', alert);
  el.querySelector('.method').textContent = methodText(l);
  const ago = nowSec() - l.last_obs_ts;
  // 관측 뒤에 쓴 추정 비용. 한도 값이 실제보다 뒤처진 정도를 가늠하게 한다
  const spent = l.since_obs ? l.since_obs.cost_micros_est : 0;
  // 칸이 좁아 둘을 함께 쓰지 못한다. 비용이 있으면 경과 시간 대신 비용을 보인다
  el.querySelector('.obs').textContent = `관측 ${clock(l.last_obs_ts)}` +
    (spent >= SINCE_OBS_SHOW_MICROS ? ` · +${usd(spent)}` : ago >= 600 ? ` (${span(ago)} 전)` : '');
  el.querySelector('.reset').textContent = l.verdict === 'reset' ? '' :
    `리셋 ${clock(l.resets_at)} · ${span(l.resets_at - nowSec())} 남음`;
}

// ── 중앙: 누적 막대 ─────────────────────────────────
function niceMax(v) {
  if (v <= 0) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  // 절반 눈금도 소수 한 자리로 떨어지는 단계만 쓴다(2.5의 절반 1.25는 1.3으로 반올림돼 틀리게 보인다)
  return [1, 2, 4, 6, 8, 10].map(m => m * e).find(m => m >= v);
}

// 위쪽 끝만 4px 둥근 막대(기준선 쪽은 각진 끝)
function topRounded(x, y, w, h, r) {
  r = Math.min(r, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function renderChart(svg, burn, keys) {
  if (!svg.getClientRects().length) return;      // 숨겨진 차트(위젯의 작은 구성)는 그리지 않는다
  const W = svg.clientWidth || svg.parentNode.clientWidth;
  const H = svg.clientHeight || +svg.getAttribute('height');
  // 치수는 차트 글자 크기(u)에 비례한다. 키오스크는 u=20px, 배경화면은 화면 크기에 따라 달라진다
  const u = parseFloat(getComputedStyle(svg).fontSize) || 20;
  const right = u * 3.2, top = u * 0.3, bottom = 2;
  const plotW = W - right, plotH = H - top - bottom;
  const n = burn.minutes;
  const totals = Array.from({ length: n }, (_, i) => keys.reduce((s, [k]) => s + burn.series[k][i], 0));
  const max = niceMax(Math.max(...totals));
  const slot = plotW / n, bw = Math.min(u * 1.2, slot * 0.7);
  const radius = u * 0.2;
  const css = getComputedStyle(document.documentElement);
  const y = v => top + plotH - (v / max) * plotH;
  let out = '';
  for (const frac of [0.5, 1]) {
    const gy = y(max * frac);
    out += `<line x1="0" x2="${plotW}" y1="${gy}" y2="${gy}" stroke="var(--grid)" stroke-width="1"/>`;
    out += `<text x="${W - u * 0.2}" y="${gy + u * 0.35}" text-anchor="end">${compact(max * frac)}</text>`;
  }
  out += `<line x1="0" x2="${plotW}" y1="${top + plotH}" y2="${top + plotH}" stroke="var(--baseline)" stroke-width="1"/>`;
  for (let i = 0; i < n; i++) {
    if (!totals[i]) continue;
    const x = i * slot + (slot - bw) / 2;
    let base = top + plotH;
    const segs = keys.map(([k, v]) => [burn.series[k][i], css.getPropertyValue(v).trim()]).filter(([v]) => v > 0);
    segs.forEach(([v, color], j) => {
      const h = (v / max) * plotH;
      const isTop = j === segs.length - 1;
      const gap = j > 0 ? 2 : 0;                        // 세그먼트 사이 2px 표면 간격
      const hh = Math.max(0, h - gap);
      const yy = base - h;
      out += isTop
        ? `<path d="${topRounded(x, yy, bw, hh, radius)}" fill="${color}"/>`
        : `<rect x="${x}" y="${yy}" width="${bw}" height="${hh}" fill="${color}"/>`;
      base = yy;
    });
  }
  svg.innerHTML = out;
}

function renderAxis(burn) {
  const t0 = burn.start_ts, n = burn.minutes;
  const marks = [0, 15, 30, 45, n - 1].map(i => `<span>${clock(t0 + i * 60)}</span>`);
  $('axis').innerHTML = marks.join('');
}

function renderBurn(s) {
  renderChart($('chart-cache'), s.burn, SERIES.cache);
  renderChart($('chart-io'), s.burn, SERIES.io);
  renderAxis(s.burn);
  const sum = Object.values(s.burn.series).flat().reduce((a, b) => a + b, 0);
  $('burn-total').textContent = `합계 ${compact(sum)}`;
}

// ── 유휴 화면 ───────────────────────────────────────
function renderIdle(s) {
  const last = s.last_activity_ts;
  $('idle-last').textContent = last ? `마지막 활동 ${clock(last)} · ${span(nowSec() - last)} 전` : '활동 기록 없음';
  for (const [w, id] of [['5h', 'idle-r5'], ['7d', 'idle-r7']]) {
    const l = s.limits[w];
    const live = l && l.verdict !== 'reset';
    $(id).textContent = live ? span(l.resets_at - nowSec()) : '—';
    $(id + '-at').textContent = live ? `${clock(l.resets_at)} · 현재 ${l.pct}%` : '';
  }
  const t = s.today;
  $('idle-today').textContent = `${compact(t.input + t.output + t.cache_read + t.cache_creation)} 토큰`;
  $('idle-today-sub').textContent = `요청 ${t.requests} · 추정 ${usd(t.cost_micros_est)}`;
}

// ── 우: 세션·수집 상태 ───────────────────────────────
function renderSide(s) {
  const cur = s.sessions[0];
  $('sess-label').textContent = cur ? `현재 세션 · ${cur.client === 'claude-code-desktop' ? 'Desktop' : 'CLI'}` : '현재 세션';
  // 세션에 모델 정보가 없으면(OTel을 보내지 않는 세션) 마지막 요청의 모델을 쓴다
  const fallback = s.last_request || {};
  const model = cur?.model || fallback.model;
  const effort = cur?.model ? cur.effort : fallback.effort;
  $('model').textContent = modelName(model) || '—';
  $('chips').innerHTML = effort ? `<span>effort ${esc(effort)}</span>` : '';
  $('ctx').textContent = cur?.ctx_pct != null ? `${Math.round(cur.ctx_pct)}%` : '—';
  $('cache').textContent = cur?.cache_hit != null ? `${Math.round(cur.cache_hit * 100)}%` : '—';
  $('cost').textContent = usd(s.today.cost_micros_est);
  $('reqs').textContent = String(s.today.requests);
  const extra = Object.values(s.limits).filter(l => l.window.startsWith('7d:') && l.verdict !== 'reset');
  $('extra-windows').textContent = extra.length
    ? '모델별 주간 ' + extra.map(l => `${l.window.slice(3)} ${l.pct}%`).join(' · ') : '';
  // 앱 정보(세션 메타데이터)는 문제가 있을 때만 보인다. 늘 보이면 '크게' 구성에서 한 줄이 늘어 차트와 겹친다
  const rows = s.health.filter(h => !(h.source === 'appmeta' && h.status === 'live'));
  $('health').innerHTML = rows.map(h =>
    // 2초마다 갱신되는 앱 정보가 오래 소식이 없으면 '읽지 못함'으로 쓴다
    `<div><span class="dot ${esc(h.status)}"></span>${esc(HEALTH_NAMES[h.source] || h.source)} · ${
      h.source === 'appmeta' && h.status === 'idle' ? '읽지 못함' : esc(HEALTH_TEXT[h.status])} · ${clock(h.last_seen)}</div>`
  ).join('');
  const hits = s.limit_hits;
  $('hits').innerHTML = hits.count_5h
    ? `<span class="icon-alert"></span>429 한도 응답 ${hits.count_5h}회 · 마지막 ${clock(Math.floor(hits.last_ts / 1000))}` : '';
}

// ── 세션 상태 ──────────────────────────────────────
// 위젯(#live, #live-mini)과 세션 페이지(#live-table)가 쓴다. 키오스크에는 이 요소가 없어 그리지 않는다
const STATE_TEXT = { working: '작업 중', waiting: '승인·응답 대기', decision: '결정 필요', done: '완료', error: '오류' };
const STATE_ORDER = ['waiting', 'decision', 'error', 'done', 'working'];
const LIVE_BASE_ITEMS = 5;         // 위젯에 기본으로 보이는 세션 수. 넘치면 아래에 '외 N개' 한 줄

// 위젯에 보일 세션 수: 기본 5개. 완료가 아닌 세션(대기·결정·오류·작업 중)이 5개를 넘으면 그것들은 모두 보인다.
// 목록은 완료가 맨 아래로 정렬돼 오므로, 앞에서부터 이 개수만큼 자르면 완료만 '외 N개'로 접힌다
function liveLimit(items) {
  const active = items.filter(i => i.state !== 'done').length;
  return Math.max(LIVE_BASE_ITEMS, active);
}
const esc = v => String(v ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function liveTitle(i) {
  // 제목이 없으면 폴더 이름, 그것도 없으면 세션 ID 앞부분
  return i.title || (i.cwd || '').split(/[\\/]/).filter(Boolean).pop() || i.session_id.slice(0, 8);
}
function liveStatus(i) {
  return STATE_TEXT[i.state] + (i.detail ? ' · ' + i.detail : '');
}

// 앱의 그 세션으로 이동하는 링크(widget_host.session_url과 같은 규칙). 앱 세션 ID를 모르면 null
const APP_SESSION_RE = /^local_[A-Za-z0-9-]{1,64}$/;
const sessionLink = appId => (appId && APP_SESSION_RE.test(appId) ? `claude://code/continue?session=${appId}` : null);

async function markSeen(id, appId, openApp = true) {
  // 1단계: 확인함으로 표시한다(실패해도 다음 스냅샷에서 다시 보인다)
  try { await fetch('/api/sessions/' + encodeURIComponent(id) + '/seen' + TOKEN_QUERY, { method: 'POST' }); } catch (e) { /* 무시 */ }
  // 2단계: 위젯 창 안이면 Claude 앱의 그 세션으로 이동한다(세션을 모르면 앱만 띄움)
  const api = window.pywebview && window.pywebview.api;
  if (openApp && api && api.open_app) api.open_app(appId || null);
}

function renderLive(s) {
  const live = s.live || { items: [], counts: {}, unseen: {} };
  const items = live.items;
  const now = nowSec();
  // 1) 위젯 띠: 기본 5개, 완료 아닌 세션이 더 많으면 모두(대기·진행 중이 위, 완료는 아래). 넘치면 '외 N개' 한 줄
  const box = $('live');
  let rows = 0;
  if (box) {
    const shown = items.slice(0, liveLimit(items));
    const html = shown.map(i =>
      `<div class="live-row${i.seen ? '' : ' unseen'}${i.stale ? ' stale' : ''}" data-state="${i.state}" data-seen="${esc(i.session_id)}" data-app="${esc(i.app_session_id || '')}" title="${esc(liveTitle(i) + ' — ' + liveStatus(i))}">`
      + `<span class="dot-st"></span><span class="t">${esc(liveTitle(i))}</span>`
      + `<span class="s">${esc(liveStatus(i))}</span><span class="e">${span(now - i.state_ts)}</span></div>`);
    if (items.length > shown.length) html.push(`<div class="live-row more">외 ${items.length - shown.length}개</div>`);
    box.innerHTML = html.join('');
    box.hidden = !items.length;
    rows = html.length;
  }
  // 2) mini: 개수가 있는 상태만 점과 숫자로
  const mini = $('live-mini');
  let miniCounts = 0;
  if (mini) {
    const parts = STATE_ORDER.filter(st => live.counts[st]).map(st =>
      `<span data-state="${st}" title="${STATE_TEXT[st]}"><i></i>${live.counts[st]}</span>`);
    mini.innerHTML = parts.join('');
    miniCounts = parts.length;
  }
  if (window.onLiveRows) window.onLiveRows(rows, miniCounts);      // widget.js: 창 크기 맞추기
  // 3) 세션 페이지 표
  const table = $('live-table');
  if (table) {
    table.innerHTML = items.length ? items.map(i =>
      `<tr class="${i.seen ? '' : 'unseen'}${i.stale ? ' stale' : ''}" data-state="${i.state}">`
      + `<td><span class="dot-st"></span>${STATE_TEXT[i.state]}</td>`
      + `<td>${sessionLink(i.app_session_id) ? `<a href="${sessionLink(i.app_session_id)}" title="앱에서 이 세션 열기">${esc(liveTitle(i))}</a>` : esc(liveTitle(i))}</td>`
      + `<td class="muted">${esc(i.cwd)}</td><td>${esc(i.detail)}</td><td class="tnum">${span(now - i.state_ts)}</td>`
      + `<td>${i.seen ? '' : `<button data-seen="${esc(i.session_id)}">확인</button>`}</td></tr>`).join('')
      : '<tr><td colspan="6" class="muted">최근 2시간 안에 활동한 세션이 없습니다</td></tr>';
  }
}

// 세션 줄(위젯)을 누르면 확인함 + 앱의 그 세션으로 이동, 확인 버튼(세션 페이지)은 확인함만
document.addEventListener('click', e => {
  const el = e.target.closest('[data-seen]');
  if (el) markSeen(el.dataset.seen, el.dataset.app, el.classList.contains('live-row'));
});

// ── 전체 ───────────────────────────────────────────
function render() {
  if (!snap) return;
  renderLive(snap);
  if (!$('lim-5h')) return;                 // 세션 페이지에는 한도 화면이 없다
  document.body.classList.toggle('idle', snap.mode === 'idle');
  renderLimit($('lim-5h'), snap.limits['5h']);
  renderLimit($('lim-7d'), snap.limits['7d']);
  if (snap.mode === 'idle') renderIdle(snap); else renderBurn(snap);
  renderSide(snap);
}

function connect() {
  const es = new EventSource('/stream' + TOKEN_QUERY);
  es.addEventListener('snapshot', e => {
    snap = JSON.parse(e.data);
    lastMessageAt = lastHeartbeatAt = Date.now();
    document.body.classList.remove('offline');
    render();
  });
  es.addEventListener('heartbeat', () => { lastHeartbeatAt = Date.now(); });
  es.onerror = () => markOffline();   // EventSource가 스스로 재연결한다
}

function markOffline() {
  document.body.classList.add('offline');
  $('offline-text').textContent = '수집기 연결 끊김 · 마지막 수신 ' +
    (lastMessageAt ? new Date(lastMessageAt).toLocaleTimeString('ko-KR', { hour12: false }) : '없음');
}

// 남은 시간 문구는 15초마다 다시 쓴다(서버 스냅샷은 30초마다 온다)
setInterval(() => {
  if (Date.now() - lastHeartbeatAt > HEARTBEAT_TIMEOUT_MS) markOffline();
  render();
}, 15000);

// 번인 방지: 5분마다 전체를 0~2px 이동
let shiftIdx = 0;
setInterval(() => {
  shiftIdx = (shiftIdx + 1) % SHIFTS.length;
  const [dx, dy] = SHIFTS[shiftIdx];
  const stage = $('stage');                 // 세션 페이지에는 없다
  if (stage) stage.style.transform = `translate(${dx}px, ${dy}px)`;
}, SHIFT_EVERY_MS);

// 창 크기가 바뀌면 차트를 다시 그린다(배경화면은 모니터마다 크기가 다르다)
addEventListener('resize', render);

connect();
