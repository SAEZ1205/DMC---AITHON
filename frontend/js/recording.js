// Vista de clase: reproductor, capítulos, resumen, «Lo que te perdiste», chat, quiz, micro-pregunta y banner.
import {
  api, beacon, esc, fmtDate, fmtTime, linkTimestamps, pct, qs, qsa, renderMarkdown, requireUser, toast,
} from './api.js';
import { createPlayer, WatchTracker } from './player.js';
import { markActive } from './tracker.js';

const user = requireUser();
const params = new URLSearchParams(location.search);
const sessionId = Number(params.get('session'));
const FLUSH_MS = 15_000;

let detail, player, tracker;
let seen = [];
let keyOnly = false;

// ---------- rangos ----------
function union(ranges) {
  const out = [];
  for (const [a, b] of [...ranges].sort((x, y) => x[0] - y[0])) {
    const last = out[out.length - 1];
    if (last && a <= last[1]) last[1] = Math.max(last[1], b); else out.push([a, b]);
  }
  return out;
}
function overlap(ranges, a, b) {
  return ranges.reduce((acc, [s, e]) => acc + Math.max(0, Math.min(e, b) - Math.max(s, a)), 0);
}
const keyChapters = () => detail.chapters.filter(c => c.is_key);
function localCoverage() {
  const keys = keyChapters().length ? keyChapters() : [{ start: 0, end: detail.session.duration_s }];
  const total = keys.reduce((acc, c) => acc + (c.end - c.start), 0);
  return total ? keys.reduce((acc, c) => acc + overlap(seen, c.start, c.end), 0) / total : 0;
}

// ---------- render ----------
function renderHeader() {
  const s = detail.session;
  document.title = `Sesión ${s.number}: ${s.title}`;
  qs('#sessionTitle').textContent = `Sesión ${s.number} – ${s.title}`;
  qs('#sessionCrumb').textContent = `Sesión ${s.number}`;
  const minutes = Math.round(s.duration_s / 60);
  const length = minutes % 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${minutes / 60} h`;
  qs('#sessionMeta').textContent = `${fmtDate(s.starts_at, { weekday: 'long', day: 'numeric', month: 'long' })} · ${length} de clase`
    + (s.approx_times ? ' · los minutos de la transcripción son aproximados' : '');
}

function renderBanner(banner = detail.banner) {
  const el = qs('#banner');
  el.className = `sol-banner${banner ? ` sol-banner-${banner.tone}` : ' hidden'}`;
  if (!banner) return;
  const icon = { ok: 'fa-circle-check', warn: 'fa-hourglass-half', bad: 'fa-circle-xmark' }[banner.tone];
  el.innerHTML = `<i class="fa-solid ${icon}"></i><span>${esc(banner.text)}</span>`;
}

function renderMeters() {
  const c = detail.compliance;
  const min = detail.course.key_coverage_min;
  const cov = Math.max(c?.key_coverage ?? 0, localCoverage());
  const best = c?.best_quiz;
  const pass = detail.course.quiz_pass_score;
  qs('#meters').innerHTML = `
    <div class="sol-meter">
      <div class="sol-meter-label"><span><i class="fa-solid fa-key"></i> Tramos clave vistos</span><strong>${pct(cov)}</strong></div>
      <div class="sol-bar"><div class="sol-bar-fill${cov >= min ? ' ok' : ''}" style="width:${Math.min(100, cov * 100)}%"></div>
        <span class="sol-bar-mark" style="left:${min * 100}%" title="Mínimo ${pct(min)}"></span></div>
      <small>Mínimo para recuperar: ${pct(min)}</small>
    </div>
    <div class="sol-meter">
      <div class="sol-meter-label"><span><i class="fa-solid fa-clipboard-check"></i> Quiz</span>
        <strong>${best == null ? 'Sin intentos' : `${best}/${detail.course.questions_per_quiz}`}</strong></div>
      <div class="sol-bar"><div class="sol-bar-fill${best >= pass ? ' ok' : ''}" style="width:${best == null ? 0 : (best / detail.course.questions_per_quiz) * 100}%"></div>
        <span class="sol-bar-mark" style="left:${(pass / detail.course.questions_per_quiz) * 100}%" title="Aprueba con ${pass}"></span></div>
      <small>Apruebas con ${pass} de ${detail.course.questions_per_quiz}</small>
    </div>`;
}

function renderChapters() {
  const unvalidated = keyChapters().length && !detail.keys_validated;
  qs('#chaptersNote').innerHTML = unvalidated
    ? '<i class="fa-solid fa-circle-info"></i> Tramos clave propuestos automáticamente, pendientes de validación del docente.' : '';
  const t = player?.currentTime ?? 0;
  qs('#chapters').innerHTML = detail.chapters.map(c => {
    const ratio = c.end > c.start ? overlap(seen, c.start, c.end) / (c.end - c.start) : 0;
    const current = t >= c.start && t < c.end;
    return `<li class="sol-chapter${c.is_key ? ' key' : ''}${current ? ' current' : ''}">
      <button type="button" data-seek="${c.start}" aria-label="Ir a ${esc(c.title)}">
        <span class="sol-chapter-time">${fmtTime(c.start)}</span>
        <span class="sol-chapter-body"><span class="sol-chapter-title">${esc(c.title)}</span>
          ${c.is_key ? `<span class="sol-key-tag"><i class="fa-solid fa-key"></i> Tramo clave${c.validated ? '' : ' · sin validar'}</span>` : ''}
          <span class="sol-chapter-bar"><span style="width:${Math.min(100, ratio * 100)}%"></span></span></span>
        <span class="sol-chapter-dur">${Math.round((c.end - c.start) / 60)} min</span>
      </button></li>`;
  }).join('');
}

async function renderSummary() {
  const el = qs('#tab-resumen');
  let data;
  try { data = await api(`/sessions/${sessionId}/summary`); } catch { data = null; }
  if (!data?.chapters.length) {
    el.innerHTML = detail.summary ? `<div class="sol-prose">${renderMarkdown(detail.summary)}</div>`
      : '<p class="sol-muted">El resumen de esta sesión aún no está disponible.</p>';
    return;
  }
  const slides = c => !c.slide_from ? '' : c.slide_from === c.slide_to ? ` · diapositiva ${c.slide_from}`
    : ` · diapositivas ${Math.min(c.slide_from, c.slide_to)}–${Math.max(c.slide_from, c.slide_to)}`;
  el.innerHTML = `<p class="sol-summary-header">${esc(data.header)}</p>
    <ol class="sol-summary">${data.chapters.map(c => `<li class="${c.is_key ? 'key' : ''}${c.logistics ? ' logistics' : ''}">
      <div class="sol-summary-head">
        <button type="button" class="sol-ts" data-seek="${c.start}">${fmtTime(c.start)}</button>
        <strong>${esc(c.title)}</strong>
        ${c.is_key ? '<span class="sol-key-tag"><i class="fa-solid fa-key"></i> Tramo clave</span>' : ''}
        <span class="sol-muted">${Math.round((c.end - c.start) / 60)} min${slides(c)}</span>
      </div>
      ${c.bullets.length ? `<ul>${c.bullets.map(b => `<li>${esc(b)}</li>`).join('')}</ul>` : ''}
      ${c.text ? `<p>${linkTimestamps(esc(c.text))}</p>` : ''}
      ${c.key_sentences.map(k => `<blockquote>«${esc(k.text)}»
        <button type="button" class="sol-ts" data-seek="${k.start}">${fmtTime(k.start)}</button></blockquote>`).join('')}
    </li>`).join('')}</ol>
    ${data.approx_times ? '<p class="sol-muted">Los minutos son aproximados: la transcripción no traía marcas de tiempo.</p>' : ''}`;
}

function renderMissed() {
  const m = detail.missed;
  qs('[data-tab="perdiste"]').classList.toggle('hidden', !m);
  if (!m) return;
  const ranges = m.absent_ranges.map(([a, b]) => `${fmtTime(a)}–${fmtTime(b)}`).join(', ');
  const chapters = detail.chapters.filter(c => m.chapter_ids.includes(c.id));
  qs('#tab-perdiste').innerHTML = `
    <p class="sol-missed-intro">${m.attended ? `Estuviste fuera de la clase en vivo ${m.absent_minutes} min (${ranges}).`
      : 'No asististe a la clase en vivo.'} Estos son los capítulos que te perdiste:</p>
    <ul class="sol-missed-list">${chapters.map(c => `<li>
      <div class="sol-missed-head"><strong>${esc(c.title)}</strong>${c.is_key ? '<span class="sol-key-tag"><i class="fa-solid fa-key"></i> Tramo clave</span>' : ''}</div>
      <p>${linkTimestamps(esc(c.summary))}</p>
      <button type="button" class="sol-btn sol-btn-sm" data-seek="${c.start}"><i class="fa-solid fa-play"></i> Ir a este tramo (${fmtTime(c.start)})</button>
    </li>`).join('')}</ul>`;
}

// ---------- reproducción ----------
function seek(seconds, play = true) {
  player.currentTime = seconds;
  if (play) player.play()?.catch?.(() => {});
  qs('#player').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function handleKeyOnly() {
  if (!keyOnly || player.paused) return;
  const t = player.currentTime;
  const keys = keyChapters();
  if (keys.some(c => t >= c.start && t < c.end - 0.5)) return;
  const next = keys.find(c => c.start > t);
  if (next) player.currentTime = next.start; else { player.pause(); toast('Terminaste los tramos clave de esta clase.', 'ok'); }
}

let lastChapterRender = 0;
function onTime() {
  markActive();
  handleKeyOnly();
  if (Date.now() - lastChapterRender > 2000) {
    lastChapterRender = Date.now();
    seen = union([...seen, ...tracker.pending, ...(tracker.current ? [tracker.current] : [])]);
    renderChapters();
    renderMeters();
  }
}

async function flush({ leaving = false } = {}) {
  const ranges = tracker.take();
  if (!ranges.length) return;
  seen = union([...seen, ...ranges]);
  const body = { session_id: sessionId, ranges };
  if (leaving) { beacon('/watch', body); return; }
  try {
    const res = await api('/watch', { method: 'POST', body });
    const before = detail.compliance?.status;
    if (res.status && res.status !== before) toast(res.status === 'recuperada' ? '¡Sesión recuperada! Ya cuenta para tu asistencia.' : 'Tu avance se guardó.', 'ok');
    await refresh();
  } catch { /* se reintenta en el siguiente envío */ }
}

async function refresh() {
  const fresh = await api(`/sessions/${sessionId}`);
  detail = { ...fresh };
  seen = union([...seen, ...detail.watched_ranges]);
  renderBanner();
  renderMeters();
  renderChapters();
}

// ---------- pestañas ----------
function selectTab(name) {
  qsa('.sol-tab').forEach(b => {
    const on = b.dataset.tab === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', String(on));
  });
  qsa('.sol-tabpanel').forEach(p => p.classList.toggle('hidden', p.id !== `tab-${name}`));
  if (name === 'chat') qs('#chatInput')?.focus({ preventScroll: true });
}

// ---------- chat ----------
function bubble(role, html, extraClass = '') {
  const el = document.createElement('div');
  el.className = `sol-msg sol-msg-${role} ${extraClass}`;
  el.innerHTML = html;
  qs('#chatLog').append(el);
  qs('#chatLog').scrollTop = qs('#chatLog').scrollHeight;
  return el;
}
const LEVELS = { 1: 'Ubicar', 2: 'Pista', 3: 'Paso guiado', 4: 'Explicación' };
const EVALUATIONS = { correcta: ['ok', 'Respuesta correcta'], parcial: ['warn', 'Respuesta parcial'], incorrecta: ['bad', 'Aún no'] };

// m: {text|answer, link, level, evaluation, slides, buttons, asks_student, thread_id, closed}
function assistantHtml(m, live = false) {
  const text = m.answer ?? m.text;
  const head = [
    m.level ? `<span class="sol-level">Nivel ${m.level} · ${LEVELS[m.level]}</span>` : '',
    m.evaluation ? `<span class="sol-eval sol-eval-${EVALUATIONS[m.evaluation][0]}">${EVALUATIONS[m.evaluation][1]}</span>` : '',
  ].join('');
  const slides = (m.slides || []).map(s => `<details class="sol-slide-ref"><summary><i class="fa-regular fa-file-powerpoint"></i>
    Diapositiva ${s.n}${s.title ? ` · ${esc(s.title)}` : ''}</summary><p>${esc(s.text || '')}</p></details>`).join('');
  const link = m.link ? `<a class="sol-chat-link" href="${esc(m.link.href)}"><i class="fa-solid fa-arrow-right"></i> ${esc(m.link.label)}</a>` : '';
  const open = live && m.thread_id && !m.closed;
  const actions = open && m.buttons?.length ? `<div class="sol-chat-actions">${m.buttons.map(b =>
    `<button type="button" class="sol-btn sol-btn-ghost sol-btn-sm" data-action="${b.action}" data-thread="${m.thread_id}">${esc(b.label)}</button>`).join('')}</div>` : '';
  const reply = open && m.asks_student ? `<form class="sol-chat-reply" data-thread="${m.thread_id}">
    <input type="text" maxlength="1000" placeholder="Responde con tus palabras…" aria-label="Tu respuesta">
    <button type="submit" class="sol-btn sol-btn-sm">Responder</button></form>` : '';
  return `${head ? `<div class="sol-msg-head">${head}</div>` : ''}<div class="sol-prose">${renderMarkdown(text)}</div>${slides}${link}${actions}${reply}`;
}

function clearTurnControls() {
  qsa('#chatLog .sol-chat-actions, #chatLog .sol-chat-reply').forEach(el => el.remove());
}

async function initChat() {
  const keys = keyChapters().slice(0, 3);
  qs('#chatSuggestions').innerHTML = keys.map(c =>
    `<button type="button" class="sol-suggestion">¿Qué se explicó sobre ${esc(c.title.toLowerCase())}?</button>`).join('');
  qsa('.sol-suggestion').forEach(b => b.addEventListener('click', () => { qs('#chatInput').value = b.textContent; ask(); }));
  const history = await api(`/sessions/${sessionId}/chat`).catch(() => []);
  if (!history.length) {
    bubble('assistant', assistantHtml({ text: `Hola, ${user.name.split(' ')[0]}. Te ayudo a repasar esta clase: te digo en qué minuto se explica, te doy pistas y te pregunto para que lo expliques con tus palabras. ¿Qué quieres repasar?` }));
  }
  const lastAssistant = history.map(m => m.role).lastIndexOf('assistant');
  history.forEach((m, i) => bubble(m.role, m.role === 'assistant' ? assistantHtml(m, i === lastAssistant) : esc(m.text)));
  qs('#chatForm').addEventListener('submit', e => { e.preventDefault(); ask(); });
  qs('#chatInput').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } });
  qs('#chatLog').addEventListener('click', e => {
    const b = e.target.closest('[data-action]');
    if (b) send({ thread_id: Number(b.dataset.thread), action: b.dataset.action }, b.textContent.trim());
  });
  qs('#chatLog').addEventListener('submit', e => {
    const form = e.target.closest('.sol-chat-reply');
    if (!form) return;
    e.preventDefault();
    const message = form.querySelector('input').value.trim();
    if (message) send({ thread_id: Number(form.dataset.thread), action: 'answer', message }, message);
  });
}

async function send(body, shown) {
  clearTurnControls();
  bubble('user', esc(shown));
  const typing = bubble('assistant', '<span class="sol-typing"><i></i><i></i><i></i></span>', 'typing');
  qs('#chatSend').disabled = true;
  try {
    const res = await api(`/sessions/${sessionId}/chat`, { method: 'POST', body });
    typing.remove();
    const el = bubble('assistant', assistantHtml(res, true));
    el.querySelector('.sol-chat-reply input')?.focus({ preventScroll: true });
  } catch (e) {
    typing.remove();
    bubble('assistant', `<p>${esc(e.message)}</p>`, 'error');
  } finally {
    qs('#chatSend').disabled = false;
  }
}

function ask() {
  const input = qs('#chatInput');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  send({ message }, message);
}

async function offerMicroQuestion() {
  if (user.role !== 'alumno') return false;
  const { question } = await api(`/me/micro-question?course_id=${detail.session.course_id}`).catch(() => ({}));
  if (!question || qs('.sol-micro')) return false;
  const el = bubble('assistant', `
    <p><strong>Una pregunta rápida (opcional):</strong> ${esc(question)}</p>
    <form class="sol-micro-form"><textarea rows="2" maxlength="2000" placeholder="Escribe con tus palabras…" aria-label="Tu respuesta"></textarea>
      <div class="sol-micro-actions"><button type="button" class="sol-btn sol-btn-ghost sol-btn-sm" data-skip>Omitir</button>
      <button class="sol-btn sol-btn-sm">Responder</button></div></form>`, 'sol-micro');
  const send = async answer => {
    await api('/me/micro-question', { method: 'POST', body: { course_id: detail.session.course_id, question, answer } }).catch(() => {});
    el.querySelector('form').remove();
    if (answer) bubble('user', esc(answer));
    bubble('assistant', assistantHtml({ text: answer ? '¡Gracias! Tu respuesta ayuda a tu Asistente Académico a acompañarte mejor.' : 'Sin problema. Sigamos con la clase.' }));
  };
  qs('form', el).addEventListener('submit', e => { e.preventDefault(); send(qs('textarea', el).value.trim() || null); });
  qs('[data-skip]', el).addEventListener('click', () => send(null));
  return true;
}

// ---------- quiz ----------
function quizIntro(best, attempts) {
  const pass = detail.course.quiz_pass_score, n = detail.course.questions_per_quiz;
  qs('#tab-quiz').innerHTML = `
    <div class="sol-quiz-intro">
      <i class="fa-solid fa-clipboard-question"></i>
      <p>${n} preguntas al azar sobre los tramos clave. Apruebas con <strong>${pass} o más</strong>. Intentos ilimitados: vale tu mejor nota.</p>
      ${best != null ? `<p>Tu mejor nota: <strong>${best}/${n}</strong> en ${attempts} intento${attempts === 1 ? '' : 's'}.</p>` : ''}
      <button type="button" class="sol-btn" id="quizStart">${best != null ? 'Intentar de nuevo' : 'Empezar quiz'}</button>
    </div>`;
  qs('#quizStart').addEventListener('click', startQuiz);
}

async function loadQuizIntro() {
  try {
    const q = await api(`/sessions/${sessionId}/quiz`);
    if (!q.ready) {
      qs('#tab-quiz').innerHTML = `<div class="sol-quiz-intro"><i class="fa-solid fa-hourglass-half"></i>
        <p><strong>Quiz en preparación.</strong> El docente aún está revisando las preguntas de esta sesión. Mientras tanto puedes avanzar con los tramos clave.</p></div>`;
      return;
    }
    quizIntro(q.best, q.attempts);
  } catch (e) {
    qs('#tab-quiz').innerHTML = `<p class="sol-muted">${esc(e.message)}</p>`;
  }
}

async function startQuiz() {
  const q = await api(`/sessions/${sessionId}/quiz`);
  qs('#tab-quiz').innerHTML = `<form id="quizForm" class="sol-quiz">
    ${q.items.map((item, i) => `<fieldset class="sol-q" data-id="${item.id}">
      <legend><span>${i + 1}.</span> ${esc(item.question)}</legend>
      ${item.options.map(o => `<label class="sol-option"><input type="radio" name="q${item.id}" value="${o.idx}" required> <span>${esc(o.text)}</span></label>`).join('')}
    </fieldset>`).join('')}
    <button class="sol-btn" id="quizSubmit" disabled>Enviar respuestas</button></form>`;
  const form = qs('#quizForm');
  form.addEventListener('change', () => { qs('#quizSubmit').disabled = !form.checkValidity(); });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const answers = Object.fromEntries(qsa('.sol-q', form).map(f => [f.dataset.id, Number(qs('input:checked', f).value)]));
    qs('#quizSubmit').disabled = true;
    try { showQuizResult(await api(`/sessions/${sessionId}/quiz`, { method: 'POST', body: { answers } })); }
    catch (err) { toast(err.message, 'error'); qs('#quizSubmit').disabled = false; }
  });
}

async function showQuizResult(res) {
  qs('#tab-quiz').innerHTML = `
    <div class="sol-quiz-result ${res.passed ? 'ok' : 'bad'}">
      <strong>${res.score}/${res.total}</strong>
      <span>${res.passed ? '¡Aprobaste el quiz!' : `Necesitas ${res.pass_score} correctas. Repasa los minutos indicados e inténtalo de nuevo.`}</span>
    </div>
    <ol class="sol-quiz-review">${res.results.map(r => `<li class="${r.correct ? 'ok' : 'bad'}">
      <p><i class="fa-solid fa-${r.correct ? 'check' : 'xmark'}"></i> ${esc(r.question)}</p>
      ${res.explanations && !r.correct ? `<p class="sol-muted">Respuesta correcta: <strong>${esc(r.options[r.correct_idx])}</strong></p>` : ''}
      ${res.explanations ? `<p class="sol-explain">${linkTimestamps(esc(r.explanation))}</p>` : ''}
      ${r.review ? `<p class="sol-explain"><i class="fa-solid fa-rotate-left"></i> ${linkTimestamps(esc(r.review.text))}</p>` : ''}
    </li>`).join('')}</ol>
    ${res.explanations ? '' : '<p class="sol-muted">Las explicaciones completas aparecen cuando apruebes el quiz.</p>'}
    <div id="microNotice"></div>
    <button type="button" class="sol-btn sol-btn-ghost" id="quizAgain">Intentar de nuevo</button>`;
  qs('#quizAgain').addEventListener('click', startQuiz);
  if (res.banner) {
    const was = detail.compliance?.status;
    await refresh();
    if (res.compliance?.status === 'recuperada' && was !== 'recuperada') toast('¡Sesión recuperada! Ya cuenta para tu asistencia.', 'ok');
  }
  if (await offerMicroQuestion()) {
    qs('#microNotice').innerHTML = `<p class="sol-micro-notice"><i class="fa-regular fa-comment-dots"></i> Te dejamos una pregunta breve en el chat.
      <button type="button" class="sol-link" data-go-chat>Responder</button></p>`;
    qs('[data-go-chat]').addEventListener('click', () => selectTab('chat'));
  }
}

// ---------- inicio ----------
async function init() {
  if (!sessionId) { qs('#sessionTitle').textContent = 'Sesión no indicada'; return; }
  try {
    const [d, transcript] = await Promise.all([api(`/sessions/${sessionId}`), api(`/sessions/${sessionId}/transcript`)]);
    detail = d;
    seen = union(detail.watched_ranges);
    renderHeader();
    renderBanner();
    player = createPlayer(qs('#player'), { videoUrl: detail.session.video_url, duration: detail.session.duration_s, segments: transcript });
    tracker = new WatchTracker(player);
    player.addEventListener('timeupdate', onTime);
    player.addEventListener('play', handleKeyOnly);
    renderMeters();
    renderChapters();
    renderSummary();
    renderMissed();
    selectTab(detail.missed ? 'perdiste' : 'resumen');
    initChat();
    loadQuizIntro();
    setInterval(() => flush(), FLUSH_MS);
    window.addEventListener('pagehide', () => flush({ leaving: true }));
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush({ leaving: true }); });
    const t = Number(params.get('t'));
    if (t) seek(t, false);
  } catch (e) {
    qs('#sessionTitle').textContent = e.message;
  }
}

document.addEventListener('click', e => {
  const target = e.target.closest('[data-seek]');
  if (target && player) { e.preventDefault(); seek(Number(target.dataset.seek)); }
  const tab = e.target.closest('.sol-tab');
  if (tab) selectTab(tab.dataset.tab);
});
qs('#keyOnly')?.addEventListener('change', e => {
  keyOnly = e.target.checked;
  if (keyOnly && player) {
    const first = keyChapters().find(c => c.end > player.currentTime);
    if (first && player.currentTime < first.start) player.currentTime = first.start;
    player.play();
  }
});

init();
