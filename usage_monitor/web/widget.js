// 위젯 창의 구성 전환, 한 줄로 접기, 투명도, 창 조작.
// 창 프로그램(widget_host.py)이 있으면 pywebview.api로 창 크기와 투명도를 맞추고,
// 브라우저에서 그냥 열었을 때는 페이지 안에서만 흉내 낸다.
'use strict';

const SIZES = { mini: [200, 30], s: [300, 132], m: [300, 192], l: [300, 424] };   // widget.css의 구성별 창 크기. l은 차트(149px)가 다 들어가도록 412→424
const ORDER = ['s', 'm', 'l'];                      // ◱ 버튼이 도는 순서. mini는 – 버튼으로만 오간다
const OPACITY_LEVELS = [100, 85, 70, 55, 40];       // widget_host.py의 OPACITY_LEVELS와 맞춘다

const hostApi = () => (window.pywebview && window.pywebview.api) || null;
const store = {
  get(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* 저장 못 해도 동작한다 */ } },
};

let lastFull = 'm';        // 접기 전의 구성. 펼 때 여기로 돌아간다
let opacity = 100;

// 세션 상태 띠가 늘리는 창 크기. widget.css의 .live(줄 22px, 위 테두리·여백 5px)와 맞춘다
const LIVE_ROW_PX = 22;
const LIVE_PAD_PX = 13;            // .stage gap 8 + 테두리 1 + 위 여백 4
const MINI_COUNT_PX = 28;          // mini에서 개수 점 하나가 차지하는 폭
let liveRows = 0, liveMiniCounts = 0;

function windowSize(size) {
  // 1단계: 구성별 기본 크기
  let [w, h] = SIZES[size];
  // 2단계: 세션 띠(s·m·l) 또는 개수 점(mini) 몫을 더한다
  if (size === 'mini') w += liveMiniCounts ? 8 + liveMiniCounts * MINI_COUNT_PX : 0;
  else if (liveRows) h += LIVE_PAD_PX + liveRows * LIVE_ROW_PX;
  // 3단계: 크기 배율만큼 창을 키운다(내용은 applyScale의 zoom으로 같은 비율로 커진다)
  return [Math.round(w * scale), Math.round(h * scale)];
}

// ── 크기 배율: 위젯 위에서 Ctrl+마우스 휠(5%씩, 50~200%), 트레이 메뉴 '위젯 크기 배율' ──
const SCALE_MIN = 0.5, SCALE_MAX = 2, SCALE_STEP = 0.05;   // widget_host.py의 SCALE_MIN·SCALE_MAX와 맞춘다
let scale = 1;

function applyScale(next, tellHost = true) {
  // 1단계: 범위 안으로, 5% 단위로 맞춘다
  next = Math.min(SCALE_MAX, Math.max(SCALE_MIN, Math.round(next / SCALE_STEP) * SCALE_STEP));
  next = Math.round(next * 100) / 100;
  scale = next;
  // 2단계: 글자·차트를 통째로 키운다. 창(html) 100%를 채운 채 비율만 바뀐다
  document.documentElement.style.zoom = scale === 1 ? '' : String(scale);
  document.getElementById('btn-size').title = `구성 바꾸기 · 크기 ${Math.round(scale * 100)}% (Ctrl+마우스 휠로 조절)`;
  store.set('widget-scale', String(scale));
  // 3단계: 창 크기를 맞추고, 바꾼 값은 창 프로그램에 저장한다
  const api = hostApi();
  if (api) {
    if (tellHost) api.set_scale(scale);
    const size = document.body.dataset.size;
    if (SIZES[size]) api.set_size(size, ...windowSize(size));
  }
  render();
}
window.syncScale = s => applyScale(s, false);    // 트레이 메뉴에서 바꿨을 때(이미 저장됨) 창 프로그램이 부른다

// Ctrl+휠: 위로 굴리면 크게, 아래로 굴리면 작게. 브라우저 자체 확대는 막는다
window.addEventListener('wheel', e => {
  if (!e.ctrlKey) return;
  e.preventDefault();
  applyScale(scale + (e.deltaY < 0 ? SCALE_STEP : -SCALE_STEP));
}, { passive: false });

function applySize(size, tellHost = true) {
  if (!SIZES[size]) size = 'm';
  if (size !== 'mini') lastFull = size;
  document.body.dataset.size = size;
  document.getElementById('btn-mini').textContent = size === 'mini' ? '▢' : '–';
  document.getElementById('btn-mini').title = size === 'mini' ? '펴기' : '한 줄로 접기';
  store.set('widget-size', size);
  store.set('widget-last-full', lastFull);
  const api = hostApi();
  if (api && tellHost) api.set_size(size, ...windowSize(size));
  render();          // app.js — 보이게 된 차트를 그린다
}

// app.js의 renderLive가 줄 수·개수가 바뀔 때 부른다. 바뀌었을 때만 창 크기를 다시 맞춘다
window.onLiveRows = (rows, miniCounts) => {
  if (rows === liveRows && miniCounts === liveMiniCounts) return;
  liveRows = rows;
  liveMiniCounts = miniCounts;
  const api = hostApi();
  const size = document.body.dataset.size;
  if (api && SIZES[size]) api.set_size(size, ...windowSize(size));
};

function showOpacity(percent) {
  opacity = percent;
  document.getElementById('btn-opacity').title = `투명도 (지금 ${percent}%)`;
  if (!hostApi()) document.body.style.opacity = percent / 100;      // 브라우저 미리보기용
}
window.syncOpacity = showOpacity;                                    // 트레이 메뉴에서 바꿨을 때 창 프로그램이 부른다

function initialSize() {
  const q = new URLSearchParams(location.search).get('size');
  return SIZES[q] ? q : store.get('widget-size', 'm');
}

document.getElementById('btn-size').addEventListener('click', () => {
  const cur = document.body.dataset.size;
  applySize(cur === 'mini' ? lastFull : ORDER[(ORDER.indexOf(cur) + 1) % ORDER.length]);
});
document.getElementById('btn-mini').addEventListener('click', () => {
  applySize(document.body.dataset.size === 'mini' ? lastFull : 'mini');
});
document.getElementById('btn-opacity').addEventListener('click', () => {
  const next = OPACITY_LEVELS[(OPACITY_LEVELS.indexOf(opacity) + 1) % OPACITY_LEVELS.length];
  showOpacity(next);
  const api = hostApi();
  if (api) api.set_opacity(next);
});
document.getElementById('btn-close').addEventListener('click', () => {
  const api = hostApi();
  if (api) api.close(); else window.close();
});

lastFull = store.get('widget-last-full', 'm');
if (!ORDER.includes(lastFull)) lastFull = 'm';
applySize(initialSize(), false);
showOpacity(100);
applyScale(Number(store.get('widget-scale', '1')) || 1, false);     // 브라우저 미리보기용. 창 안에서는 아래에서 다시 맞춘다
// pywebview.api는 페이지 로드보다 늦게 붙는다. 붙으면 저장된 투명도·크기 배율을 읽어 오고 창 크기를 맞춘다
window.addEventListener('pywebviewready', () => {
  hostApi().get_state().then(state => {
    showOpacity(state.opacity);
    applyScale(state.scale || 1, false);         // 창 크기도 여기서 배율에 맞춰진다
  });
  applySize(document.body.dataset.size);
});
