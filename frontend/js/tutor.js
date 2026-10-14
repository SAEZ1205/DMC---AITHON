// Seguimiento (tutor y roles afines): contadores, tabla de alumnos, cola de nivel 2 con registro de
// contacto, detalle con línea de tiempo, indicadores semanales y validación de tramos clave.
import { api, currentCourseId, esc, fmtDateTime, fmtTime, pct, plural, qs, qsa, relativeDays, requireUser, toast } from './api.js';

requireUser({ staff: true });

const REASONS = { tiempo: 'Tiempo', trabajo: 'Trabajo', contenido: 'Contenido', economico: 'Económico', salud: 'Salud', otro: 'Otro' };
const ACTIONS = { recuperar: 'Recuperar con grabaciones', pausa: 'Pausar', otra_cohorte: 'Otra cohorte', ninguna: 'Ninguna' };
const CHANNELS = { llamada: 'Llamada', whatsapp: 'WhatsApp', email: 'Correo', reunion: 'Reunión' };
const CHOICES = { recuperar: 'quiere recuperar', pausa: 'pide pausar', otra_cohorte: 'pide otra cohorte', hablar_asistente: 'quiere hablar con el AA', confirmar_retiro: 'confirmó el retiro' };

let courseId;
let board = null;
let stateFilter = null;
let charts = [];
let currentKeys = null;

const chip = (state, label) => `<span class="sol-chip sol-chip-${state}">${esc(label)}</span>`;
const levelTag = alert => alert ? `<span class="sol-level sol-level-${alert.level}">Nivel ${alert.level}</span>` : '<span class="sol-muted">—</span>';

// ---------- alumnos ----------
async function loadBoard() {
  board = await api(`/tutor/courses/${courseId}/students`);
  qs('#courseName').textContent = `${board.course.name} · ${board.course.dictated} de ${board.course.total} sesiones dictadas`;
  qs('#clock').textContent = `Datos al ${fmtDateTime(board.now)}`;
  qs('#queueCount').innerHTML = board.level2_open ? `<span class="sol-level sol-level-2">${board.level2_open}</span>` : '';
  renderCounters();
  renderRows();
}

function renderCounters() {
  qs('#counters').innerHTML = board.counters.map(c => `
    <button type="button" class="sol-counter ${c.state}${stateFilter === c.state ? ' selected' : ''}" data-state="${c.state}" aria-pressed="${stateFilter === c.state}">
      <span>${esc(c.label)}</span><strong>${c.count}</strong></button>`).join('') + `
    <button type="button" class="sol-counter level2" data-goto="cola"><span>Nivel 2 abiertas</span><strong>${board.level2_open}</strong></button>`;
}

function renderRows() {
  const term = qs('#search').value.trim().toLowerCase();
  const rows = board.students.filter(s => (!stateFilter || s.state === stateFilter)
    && (!term || s.name.toLowerCase().includes(term) || s.email.toLowerCase().includes(term)));
  qs('#studentRows').innerHTML = rows.length ? rows.map(s => `
    <tr tabindex="0" data-user="${s.user_id}">
      <td><strong>${esc(s.name)}</strong><br><small class="sol-muted">${esc(s.email)}</small></td>
      <td>${chip(s.state, s.state_label)}</td>
      <td class="sol-wrap"><ul class="sol-reasons">${s.reasons.slice(0, 2).map(r => `<li>${esc(r)}</li>`).join('')}${s.reasons.length > 2 ? `<li class="sol-muted">+${s.reasons.length - 2} más</li>` : ''}</ul></td>
      <td>${pct(s.effective)}</td>
      <td><strong style="color:${s.projection < s.threshold ? 'var(--sol-red)' : 'inherit'}">${pct(s.projection)}</strong></td>
      <td>${s.pending || '—'}</td>
      <td>${relativeDays(s.last_activity, board.now)}</td>
      <td>${levelTag(s.alert)}</td>
    </tr>`).join('') : '<tr><td colspan="8" class="sol-empty">Sin alumnos para este filtro.</td></tr>';
}

// ---------- detalle ----------
async function openStudent(userId) {
  const drawer = qs('#drawer');
  drawer.innerHTML = '<p class="sol-loading">Cargando…</p>';
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
  const t = await api(`/tutor/students/${userId}/timeline?course_id=${courseId}`);
  const s = t.student, f = t.features;
  const events = [
    ...t.state_changes.map(c => ({ at: c.at, html: `Pasó a ${chip(c.state, c.state_label)}<br><small class="sol-muted">${c.reasons.map(esc).join(' · ')}</small>` })),
    ...t.alerts.flatMap(a => [
      { at: a.created_at, html: `Alerta ${a.escalated_at && a.escalated_at === a.created_at ? 'de nivel 2' : 'de nivel 1'} creada` },
      ...(a.escalated_at && a.escalated_at !== a.created_at ? [{ at: a.escalated_at, html: '<span class="sol-level sol-level-2">Escalada a nivel 2</span> (48 h sin actividad o inactivo)' }] : []),
      ...a.contacts.map(c => ({ at: c.at, html: `Contacto por ${esc(CHANNELS[c.channel] || c.channel)} (${esc(c.by)}): ${c.responded ? 'respondió' : 'no respondió'}${c.reason ? ` · motivo ${esc(REASONS[c.reason] || c.reason)}` : ''}${c.action ? ` · ofreció ${esc(ACTIONS[c.action] || c.action)}` : ''}${c.result ? `<br><small>${esc(c.result)}</small>` : ''}` })),
      ...(a.closed_at ? [{ at: a.closed_at, html: `Alerta cerrada${a.outcome ? `: ${esc(a.outcome)}` : ''}` }] : []),
    ]),
    ...t.messages.map(m => ({ at: m.at, html: `<i class="fa-regular fa-bell"></i> Aviso automático: «${esc(m.text)}» ${m.read ? '(leído)' : '(no leído)'}` })),
    ...t.micro.map(m => ({ at: m.at, html: `<i class="fa-regular fa-comment-dots"></i> Micro-pregunta: «${esc(m.question)}»<br>${m.answer ? `<em>${esc(m.answer)}</em> <small class="sol-muted">(${esc(m.topic)}, ${esc(m.sentiment)})</small>` : '<small class="sol-muted">La omitió</small>'}` })),
    ...t.withdrawals.map(w => ({ at: w.at, html: `<i class="fa-solid fa-door-open"></i> Solicitud de retiro: motivo ${esc(REASONS[w.reason] || w.reason)}; ${esc(CHOICES[w.choice] || w.choice || '')}${w.comment ? `<br><small>«${esc(w.comment)}»</small>` : ''}` })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  const recent = t.sessions.slice(-10).reverse();
  const flag = (on, label) => `<div class="sol-feature${on ? ' flag' : ''}">${label}<strong>${on ? 'Sí' : 'No'}</strong></div>`;
  drawer.innerHTML = `
    <div class="sol-drawer-head">
      <div><h2>${esc(s.name)}</h2><small class="sol-muted">${esc(s.email)}</small><div style="margin-top:6px">${chip(s.state, s.state_label)} ${levelTag(s.alert)}</div></div>
      <button type="button" class="sol-close" aria-label="Cerrar detalle" data-close-drawer><i class="fa-solid fa-xmark"></i></button>
    </div>
    <div class="sol-drawer-body">
      <ul class="sol-reasons">${s.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>
      <div class="sol-kpis" style="margin:14px 0">
        <div class="sol-kpi"><span>Asistencia efectiva</span><strong>${pct(s.effective)}</strong><small>${s.complied}/${s.dictated} dictadas</small></div>
        <div class="sol-kpi ${s.projection < s.threshold ? 'bad' : 'ok'}"><span>Proyección</span><strong>${pct(s.projection)}</strong><small>meta ${pct(s.threshold)}</small></div>
        <div class="sol-kpi ${s.pending ? 'warn' : ''}"><span>Por recuperar</span><strong>${s.pending}</strong><small>le faltan ${s.missing}</small></div>
        <div class="sol-kpi"><span>Última actividad</span><strong style="font-size:18px">${relativeDays(s.last_activity, board.now)}</strong><small>${fmtDateTime(s.last_activity)}</small></div>
      </div>
      <h3>Sesiones</h3>
      <div class="sol-session-strip">${t.sessions.map(c => `<span class="sol-session-dot ${c.status}" title="Sesión ${c.number}: ${esc(c.status_label)} · en vivo ${pct(c.live_ratio)}${c.late_min ? ` · llegó ${Math.round(c.late_min)} min tarde` : ''}">${c.number}</span>`).join('')}</div>
      <p class="sol-legend"><span><i style="background:var(--sol-green)"></i>En vivo</span><span><i style="background:#7cc79a"></i>Recuperada</span><span><i style="background:var(--sol-amber)"></i>Por recuperar</span><span><i style="background:var(--sol-red)"></i>No cumplida</span></p>
      <h3>Variables de conducta</h3>
      <div class="sol-feature-grid">
        <div class="sol-feature">Tiempo activo (7 días)<strong>${f.active_min_week} min</strong></div>
        <div class="sol-feature${f.previous_study_hour_mode != null && f.study_hour_mode !== f.previous_study_hour_mode ? ' flag' : ''}">Hora habitual de estudio<strong>${f.study_hour_mode != null ? `${f.study_hour_mode}:00` : '—'}${f.previous_study_hour_mode != null && f.study_hour_mode !== f.previous_study_hour_mode ? ` (antes ${f.previous_study_hour_mode}:00)` : ''}</strong></div>
        <div class="sol-feature">Preguntas al chat<strong>${f.questions_content} contenido · ${f.questions_logistics} logística</strong></div>
        <div class="sol-feature${f.quiz_trend < 0 ? ' flag' : ''}">Tendencia del quiz<strong>${f.quiz_trend == null ? '—' : f.quiz_trend > 0 ? '↗ sube' : f.quiz_trend < 0 ? '↘ baja' : '→ estable'}</strong></div>
        ${flag(f.late_shift, 'Llega tarde / sale antes')}
        ${flag(f.pattern_drop, 'Caída de asistencia en vivo')}
        ${flag(f.mode_shift, 'Pasó a recuperar')}
      </div>
      <h3>Últimas sesiones</h3>
      <div class="sol-table-wrap"><table class="sol-table sol-sessions-table">
        <thead><tr><th>#</th><th>Estado</th><th>En vivo</th><th>Tarde</th><th>Sale antes</th><th>Chat/voz</th><th>Tramos</th><th>Quiz</th></tr></thead>
        <tbody>${recent.map(c => `<tr><td>${c.number}</td><td>${chip(c.status, c.status_label)}</td><td>${pct(c.live_ratio)}</td>
          <td>${c.late_min != null ? `${Math.round(c.late_min)} min` : '—'}</td><td>${c.early_leave_min != null ? `${Math.round(c.early_leave_min)} min` : '—'}</td>
          <td>${c.chat_msgs}/${c.voice_turns}</td><td>${pct(c.key_coverage)}</td><td>${c.best_quiz ?? '—'}</td></tr>`).join('')}</tbody>
      </table></div>
      <h3>Línea de tiempo</h3>
      ${events.length ? `<ul class="sol-timeline">${events.map(e => `<li><time>${fmtDateTime(e.at)}</time>${e.html}</li>`).join('')}</ul>` : '<p class="sol-muted">Sin eventos registrados.</p>'}
    </div>`;
  qs('[data-close-drawer]', drawer).focus();
}

function closeDrawer() {
  const drawer = qs('#drawer');
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
}

// ---------- cola de nivel 2 ----------
async function loadQueue() {
  const items = await api(`/tutor/alerts?course_id=${courseId}&level=2`);
  qs('#queue').innerHTML = items.length ? items.map(a => `
    <article class="sol-queue-item">
      <div class="sol-queue-head">
        <strong>${esc(a.student.name)}</strong>${a.student.state ? chip(a.student.state, a.student.state_label) : ''}
        <span class="sol-queue-meta"><i class="fa-regular fa-clock"></i> En cola hace ${a.hours_open >= 48 ? `${Math.round(a.hours_open / 24)} días` : `${Math.round(a.hours_open)} h`} · ${esc(a.student.email)}</span>
      </div>
      <ul class="sol-reasons" style="margin-top:8px">${a.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>
      <p class="sol-note">Asistencia efectiva ${pct(a.student.effective)} · proyección ${pct(a.student.projection)} · ${a.student.pending} por recuperar · le faltan ${a.student.missing} sesiones</p>
      ${a.contacts.length ? `<ul class="sol-contacts">${a.contacts.map(c => `<li>${fmtDateTime(c.at)} · ${esc(CHANNELS[c.channel] || c.channel)} · ${c.responded ? 'respondió' : 'no respondió'}${c.result ? ` · ${esc(c.result)}` : ''}</li>`).join('')}</ul>` : ''}
      <div class="sol-queue-actions">
        <button type="button" class="sol-btn sol-btn-sm" data-contact="${a.id}" data-name="${esc(a.student.name)}"><i class="fa-solid fa-phone"></i> Registrar contacto</button>
        <button type="button" class="sol-btn sol-btn-ghost sol-btn-sm" data-user="${a.student.user_id}"><i class="fa-solid fa-timeline"></i> Ver detalle</button>
      </div>
    </article>`).join('') : '<p class="sol-empty"><i class="fa-solid fa-circle-check"></i> No hay alumnos en la cola de nivel 2.</p>';
}

function contactModal(alertId, name) {
  const local = board.now.slice(0, 16);
  const wrap = document.createElement('div');
  wrap.className = 'sol-modal-backdrop';
  wrap.innerHTML = `<form class="sol-modal" role="dialog" aria-modal="true" aria-labelledby="ctTitle">
    <h2 id="ctTitle"><i class="fa-solid fa-phone"></i> Registrar contacto con ${esc(name)}</h2>
    <label for="ctAt">Fecha y hora del contacto</label><input type="datetime-local" id="ctAt" value="${local}" required>
    <label for="ctChannel">Canal</label>
    <select id="ctChannel">${Object.entries(CHANNELS).map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select>
    <label>¿Respondió?</label>
    <div class="sol-options"><label><input type="radio" name="responded" value="1" required><span>Sí</span></label><label><input type="radio" name="responded" value="0"><span>No</span></label></div>
    <label>Motivo</label>
    <div class="sol-options">${Object.entries(REASONS).map(([k, l]) => `<label><input type="radio" name="reason" value="${k}"><span>${l}</span></label>`).join('')}</div>
    <label>Acción ofrecida</label>
    <div class="sol-options">${Object.entries(ACTIONS).map(([k, l]) => `<label><input type="radio" name="action" value="${k}"><span>${l}</span></label>`).join('')}</div>
    <label for="ctResult">Resultado</label><textarea id="ctResult" rows="3" maxlength="500" placeholder="Ej.: Cambió de trabajo; recuperará las sesiones 23 y 24 este fin de semana."></textarea>
    <label class="sol-switch" style="margin-top:14px"><input type="checkbox" id="ctClose"><span></span> Cerrar la alerta con este resultado</label>
    <div class="sol-modal-actions"><button type="button" class="sol-btn sol-btn-ghost" data-cancel>Cancelar</button><button class="sol-btn">Guardar contacto</button></div>
  </form>`;
  document.body.append(wrap);
  const close = () => wrap.remove();
  qs('[data-cancel]', wrap).addEventListener('click', close);
  wrap.addEventListener('click', e => { if (e.target === wrap) close(); });
  qs('#ctAt', wrap).focus();
  qs('form', wrap).addEventListener('submit', async e => {
    e.preventDefault();
    const val = name => qs(`input[name=${name}]:checked`, wrap)?.value || '';
    try {
      await api(`/tutor/alerts/${alertId}/contact`, {
        method: 'POST',
        body: {
          contacted_at: qs('#ctAt', wrap).value, channel: qs('#ctChannel', wrap).value, responded: val('responded') === '1',
          reason: val('reason'), action: val('action'), result: qs('#ctResult', wrap).value.trim(), close: qs('#ctClose', wrap).checked,
        },
      });
      close();
      toast('Contacto registrado.', 'ok');
      await Promise.all([loadQueue(), loadBoard()]);
    } catch (err) { toast(err.message, 'error'); }
  });
}

// ---------- indicadores ----------
async function loadCharts() {
  if (charts.length || !window.Chart) return;
  const { weeks } = await api(`/tutor/courses/${courseId}/stats`);
  const labels = weeks.map(w => `S${w.week}`);
  const font = { family: 'Poppins', size: 11 };
  Chart.defaults.font = font;
  const opts = extra => ({ responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } } }, ...extra });
  charts = [
    new Chart(qs('#chartModes'), {
      type: 'bar',
      data: { labels, datasets: [
        { label: 'En vivo', data: weeks.map(w => w.live), backgroundColor: '#2e9d5b' },
        { label: 'Recuperadas', data: weeks.map(w => w.recovered), backgroundColor: '#2a7bb7' },
      ] },
      options: opts({ scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } }),
    }),
    new Chart(qs('#chartQuiz'), {
      type: 'line',
      data: { labels, datasets: [
        { label: 'En vivo', data: weeks.map(w => w.quiz_live), borderColor: '#2e9d5b', backgroundColor: '#2e9d5b', tension: .3, spanGaps: true },
        { label: 'Recuperadas', data: weeks.map(w => w.quiz_recovered), borderColor: '#2a7bb7', backgroundColor: '#2a7bb7', tension: .3, spanGaps: true },
      ] },
      options: opts({ scales: { y: { min: 0, max: 5 } } }),
    }),
    new Chart(qs('#chartPart'), {
      type: 'bar',
      data: { labels, datasets: [
        { label: 'Mensajes de chat', data: weeks.map(w => w.chat), backgroundColor: '#00bfd7' },
        { label: 'Turnos de voz', data: weeks.map(w => w.voice), backgroundColor: '#7b1fa2' },
      ] },
      options: opts({ scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } }),
    }),
  ];
}

// ---------- tramos clave ----------
async function loadKeySessions() {
  const rows = await api(`/tutor/sessions?course_id=${courseId}`);
  qs('#keySessions').innerHTML = rows.filter(s => s.processed).map(s => `
    <tr tabindex="0" data-session="${s.id}"${currentKeys?.session.id === s.id ? ' class="selected"' : ''}>
      <td><strong>Sesión ${s.number}</strong><br><small class="sol-muted">${esc(s.title)}</small></td>
      <td>${pct(s.key_ratio)}</td>
      <td>${s.validated ? chip('activo', 'Validado') : chip('en_riesgo', 'Por validar')}</td>
    </tr>`).join('');
}

function keyRatio() {
  const total = currentKeys.session.duration_s;
  return currentKeys.chapters.filter(c => c.is_key).reduce((acc, c) => acc + (c.end - c.start), 0) / total;
}

function renderKeySummary() {
  const k = currentKeys;
  const ratio = keyRatio();
  const tip = ratio > 0.4
    ? 'Los tramos clave superan el 40 % de la clase: recuperar deja de ser más corto que ver la sesión entera.'
    : ratio < 0.3 ? 'Se recomienda que los tramos clave sumen entre 30 % y 40 % de la clase.' : '';
  qs('#keySummary').innerHTML = `
    <div class="sol-keys-summary"><span>Tramos clave: <strong>${pct(ratio)}</strong> de la clase (${Math.round(ratio * k.session.duration_s / 60)} min)</span>
      ${k.chapters.some(c => c.validated) ? chip('activo', 'Validado') : chip('en_riesgo', 'Propuesta automática')}</div>
    ${tip ? `<p class="sol-warning"><i class="fa-solid fa-triangle-exclamation"></i> ${tip}</p>` : ''}`;
}

function parseTime(text) {
  const parts = String(text).trim().split(':').map(Number);
  if (!parts.length || parts.some(n => Number.isNaN(n) || n < 0)) return null;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

function chapterSource(c) {
  if (c.slide_from) return c.slide_from === c.slide_to ? `Diapositiva ${c.slide_from}` : `Diapositivas ${c.slide_from}–${c.slide_to}`;
  return c.source === 'texttiling' ? 'Por cambio de tema en la transcripción' : '';
}

function renderKeyEditor() {
  const k = currentKeys;
  qs('#keyEditor').innerHTML = `
    <h2 class="sol-card-title">Sesión ${k.session.number} – ${esc(k.session.title)}</h2>
    <div id="keySummary" aria-live="polite"></div>
    <ul class="sol-keys-list">${k.chapters.map((c, i) => `<li>
      <span class="sol-chapter-range">
        <input type="text" value="${fmtTime(c.start)}" data-start="${i}" aria-label="Inicio del capítulo" inputmode="numeric">
        <input type="text" value="${fmtTime(c.end)}" data-end="${i}" aria-label="Fin del capítulo" inputmode="numeric">
      </span>
      <span><input type="text" value="${esc(c.title)}" data-title="${i}" aria-label="Título del capítulo">
        <p>${esc([chapterSource(c), c.summary].filter(Boolean).join(' · '))}</p></span>
      <span class="sol-chapter-flags">
        <label class="sol-switch"><input type="checkbox" data-key="${i}" ${c.is_key ? 'checked' : ''} ${c.logistics ? 'disabled' : ''}><span></span> Clave</label>
        <label class="sol-switch"><input type="checkbox" data-logistics="${i}" ${c.logistics ? 'checked' : ''}><span></span> Logística</label>
      </span>
    </li>`).join('')}</ul>
    <div class="sol-modal-actions"><button type="button" class="sol-btn" id="saveKeys"><i class="fa-solid fa-check"></i> Validar tramos clave</button></div>
    <div id="hardTopics" class="sol-hard-topics"></div>`;
  renderKeySummary();
}

async function renderHardTopics(sessionId) {
  const { topics } = await api(`/tutor/sessions/${sessionId}/hard-topics`).catch(() => ({ topics: [] }));
  const box = qs('#hardTopics');
  if (!box) return;
  box.innerHTML = `<h3 class="sol-card-title"><i class="fa-solid fa-person-chalkboard"></i> Temas que más cuestan</h3>
    ${topics.length ? `<p class="sol-note">Capítulos donde las consultas al tutor del chat llegaron a «paso guiado» o «explicación».</p>
    <ul>${topics.slice(0, 5).map(t => `<li><span class="sol-chip sol-chip-proxima">${fmtTime(t.start)}</span>
      <strong>${esc(t.title)}</strong>${t.is_key ? ' <i class="fa-solid fa-key sol-muted" title="Tramo clave"></i>' : ''}
      <small class="sol-muted">${t.hard} de ${plural(t.threads, 'consulta', 'consultas')} necesitaron más ayuda · ${plural(t.students, 'alumno', 'alumnos')}</small></li>`).join('')}</ul>`
    : '<p class="sol-note">Aún no hay consultas al tutor del chat sobre esta sesión.</p>'}`;
}

async function openKeys(sessionId) {
  currentKeys = await api(`/tutor/sessions/${sessionId}/chapters`);
  renderKeyEditor();
  renderHardTopics(sessionId);
  qsa('#keySessions tr').forEach(tr => tr.classList.toggle('selected', Number(tr.dataset.session) === sessionId));
}

async function saveKeys() {
  qs('#saveKeys').disabled = true;
  try {
    currentKeys = await api(`/tutor/sessions/${currentKeys.session.id}/chapters`, {
      method: 'PATCH',
      body: { chapters: currentKeys.chapters.map(c => ({ id: c.id, is_key: c.is_key, title: c.title, start: c.start, end: c.end, logistics: c.logistics })), validate_all: true },
    });
    renderKeyEditor();
    await loadKeySessions();
    toast('Tramos clave validados. Se recalculó la cobertura de los alumnos.', 'ok');
  } catch (err) { toast(err.message, 'error'); qs('#saveKeys').disabled = false; }
}

// ---------- quiz del docente ----------
const QUIZ_STATUS = { borrador: ['en_riesgo', 'Borrador'], aprobada: ['activo', 'Aprobada'], descartada: ['inactivo', 'Descartada'] };
let currentQuiz = null;
let quizEditing = null;
let quizExtras = {};

async function loadQuizSessions() {
  const rows = await api(`/tutor/sessions?course_id=${courseId}`);
  qs('#quizSessions').innerHTML = rows.filter(s => s.processed).map(s => `
    <tr tabindex="0" data-quiz-session="${s.id}"${currentQuiz?.session.id === s.id ? ' class="selected"' : ''}>
      <td><strong>Sesión ${s.number}</strong><br><small class="sol-muted">${esc(s.title)}</small></td>
      <td><small>${esc(s.quiz.text)}</small></td>
      <td>${s.quiz.active ? chip('activo', 'Activo') : chip('en_riesgo', 'En preparación')}</td>
    </tr>`).join('');
}

function quizForm(item) {
  const q = item || { question: '', options: ['', '', '', ''], answer_idx: 0, explanation: '', minute: '', slide: '' };
  const id = item ? item.id : 'new';
  return `<form class="sol-quiz-form" data-quiz-form="${id}">
    <label>Pregunta<textarea name="question" rows="2" required minlength="5">${esc(q.question)}</textarea></label>
    <fieldset><legend>Alternativas (marca la correcta)</legend>
      ${q.options.map((o, i) => `<label class="sol-quiz-option"><input type="radio" name="answer_idx" value="${i}" ${i === q.answer_idx ? 'checked' : ''} aria-label="Correcta">
        <input type="text" name="option${i}" value="${esc(o)}" required aria-label="Alternativa ${i + 1}"></label>`).join('')}
    </fieldset>
    <label>Explicación (el alumno la ve al aprobar)<textarea name="explanation" rows="2">${esc(q.explanation || '')}</textarea></label>
    <div class="sol-quiz-form-row">
      <label>Minuto<input type="text" name="minute" value="${esc(q.minute || '')}" placeholder="mm:ss" pattern="(\\d{1,2}:)?\\d{1,2}:\\d{2}"></label>
      <label>Diapositiva<input type="number" name="slide" min="1" value="${q.slide ?? ''}"></label>
    </div>
    <div class="sol-modal-actions">
      <button type="button" class="sol-btn sol-btn-ghost" data-quiz-cancel>Cancelar</button>
      <button type="submit" class="sol-btn sol-btn-ghost" data-quiz-save="guardar">Guardar borrador</button>
      ${item ? '<button type="submit" class="sol-btn" data-quiz-save="aprobar"><i class="fa-solid fa-check"></i> Guardar y aprobar</button>' : ''}
    </div>
  </form>`;
}

function quizItemCard(item) {
  const [tone, label] = QUIZ_STATUS[item.status];
  const extra = quizExtras[item.id] || {};
  const where = [item.minute ? `minuto ${item.minute}` : '', item.slide ? `diapositiva ${item.slide}` : ''].filter(Boolean).join(' · ');
  const source = item.source === 'propuesta_ia' ? `Propuesta IA${item.edited_by_teacher ? ' · editada' : ''}` : 'Docente';
  if (quizEditing === item.id) return `<li class="sol-quiz-item editing">${quizForm(item)}${quizExtraHtml(item, extra)}</li>`;
  return `<li class="sol-quiz-item ${item.status}">
    <div class="sol-quiz-item-head">${chip(tone, label)}<span class="sol-source">${esc(source)}</span>
      ${where ? `<span class="sol-muted">${esc(where)}</span>` : ''}</div>
    <p class="sol-quiz-q">${esc(item.question)}</p>
    <ol class="sol-quiz-opts">${item.options.map((o, i) => `<li class="${i === item.answer_idx ? 'correct' : ''}">${esc(o)}${i === item.answer_idx ? ' <i class="fa-solid fa-check" aria-label="correcta"></i>' : ''}</li>`).join('')}</ol>
    ${item.explanation ? `<p class="sol-muted">${esc(item.explanation)}</p>` : ''}
    ${item.approved_by ? `<p class="sol-muted"><i class="fa-solid fa-user-check"></i> Aprobada por ${esc(item.approved_by)} · ${fmtDateTime(item.approved_at)}</p>` : ''}
    ${quizExtraHtml(item, extra)}
    <div class="sol-quiz-actions">
      ${item.status !== 'aprobada' ? `<button type="button" class="sol-btn sol-btn-sm" data-quiz-action="aprobar" data-id="${item.id}"><i class="fa-solid fa-check"></i> Aprobar</button>` : ''}
      <button type="button" class="sol-btn sol-btn-sm sol-btn-ghost" data-quiz-edit="${item.id}"><i class="fa-solid fa-pen"></i> Editar</button>
      ${item.status !== 'descartada' ? `<button type="button" class="sol-btn sol-btn-sm sol-btn-ghost" data-quiz-action="descartar" data-id="${item.id}">Descartar</button>`
        : `<button type="button" class="sol-btn sol-btn-sm sol-btn-ghost" data-quiz-action="borrador" data-id="${item.id}">Recuperar como borrador</button>`}
      <button type="button" class="sol-link" data-quiz-assist="alternativas" data-id="${item.id}"><i class="fa-solid fa-wand-magic-sparkles"></i> Sugerir alternativas</button>
      <button type="button" class="sol-link" data-quiz-assist="revisar" data-id="${item.id}"><i class="fa-solid fa-spell-check"></i> Revisar redacción</button>
    </div>
  </li>`;
}

function quizExtraHtml(item, extra) {
  let html = '';
  if (extra.distractors) {
    html += `<div class="sol-quiz-suggest"><strong>Alternativas sugeridas por la IA</strong> (no se aplican solas):
      <ul>${extra.distractors.map((d, i) => `<li>${esc(d)} <button type="button" class="sol-link" data-use-distractor="${i}" data-id="${item.id}">Usar</button></li>`).join('')}</ul></div>`;
  }
  if (extra.review) {
    html += `<div class="sol-quiz-suggest"><strong>Revisión de redacción</strong>
      <ul>${extra.review.issues.map(t => `<li>${esc(t)}</li>`).join('')}</ul>
      ${extra.review.suggestion && extra.review.suggestion !== item.question ? `<p>Sugerencia: «${esc(extra.review.suggestion)}» <button type="button" class="sol-link" data-use-suggestion data-id="${item.id}">Usar</button></p>` : ''}</div>`;
  }
  return html;
}

function renderQuizEditor() {
  const q = currentQuiz;
  qs('#quizEditor').innerHTML = `
    <h2 class="sol-card-title">Sesión ${q.session.number} – ${esc(q.session.title)}</h2>
    <div class="sol-keys-summary"><span>${esc(q.counts.text)} · <strong>${q.counts.approved}</strong> aprobadas</span>
      ${q.counts.active ? chip('activo', 'Quiz activo') : chip('en_riesgo', 'En preparación')}</div>
    ${q.counts.warning ? `<p class="sol-warning"><i class="fa-solid fa-triangle-exclamation"></i> ${esc(q.counts.warning)}</p>` : ''}
    <div class="sol-quiz-toolbar">
      <button type="button" class="sol-btn sol-btn-sm" data-quiz-new><i class="fa-solid fa-plus"></i> Nueva pregunta</button>
      <button type="button" class="sol-btn sol-btn-sm sol-btn-ghost" data-quiz-assist="proponer"><i class="fa-solid fa-wand-magic-sparkles"></i> Proponer preguntas (IA)</button>
      <span class="sol-muted">Lo que propone la IA queda en borrador: revísalo y apruébalo una por una.</span>
    </div>
    ${quizEditing === 'new' ? `<div class="sol-quiz-item editing">${quizForm(null)}</div>` : ''}
    <ul class="sol-quiz-bank">${q.items.map(quizItemCard).join('')}</ul>`;
}

async function openQuiz(sessionId) {
  currentQuiz = await api(`/tutor/sessions/${sessionId}/quiz`);
  quizEditing = null;
  quizExtras = {};
  renderQuizEditor();
  qsa('#quizSessions tr').forEach(tr => tr.classList.toggle('selected', Number(tr.dataset.quizSession) === sessionId));
}

async function refreshQuiz(payload, message) {
  currentQuiz = payload;
  renderQuizEditor();
  await loadQuizSessions();
  if (message) toast(message, 'ok');
}

async function quizAction(id, action) {
  try {
    const labels = { aprobar: 'Pregunta aprobada.', descartar: 'Pregunta descartada.', borrador: 'Pregunta recuperada como borrador.' };
    await refreshQuiz(await api(`/tutor/quiz/${id}`, { method: 'PATCH', body: { action } }), labels[action]);
  } catch (err) { toast(err.message, 'error'); }
}

async function quizAssist(action, id) {
  const sid = currentQuiz.session.id;
  try {
    const res = await api(`/tutor/sessions/${sid}/quiz/assist`, { method: 'POST', body: { action, item_id: id ?? null } });
    if (action === 'proponer') return refreshQuiz(res, res.message);
    quizExtras[id] = { ...quizExtras[id], ...(action === 'alternativas' ? { distractors: res.distractors } : { review: res.review }) };
    renderQuizEditor();
  } catch (err) { toast(err.message, 'error'); }
}

function formValues(form) {
  const data = new FormData(form);
  const slide = data.get('slide');
  return {
    question: data.get('question').trim(),
    options: [0, 1, 2, 3].map(i => data.get(`option${i}`).trim()),
    answer_idx: Number(data.get('answer_idx')),
    explanation: data.get('explanation').trim(),
    minute: data.get('minute').trim() || null,
    slide: slide ? Number(slide) : null,
  };
}

async function saveQuizForm(form, mode) {
  const id = form.dataset.quizForm;
  const body = formValues(form);
  try {
    if (id === 'new') {
      quizEditing = null;
      await refreshQuiz(await api(`/tutor/sessions/${currentQuiz.session.id}/quiz`, { method: 'POST', body }), 'Pregunta guardada como borrador.');
    } else {
      quizEditing = null;
      const payload = await api(`/tutor/quiz/${id}`, { method: 'PATCH', body: { ...body, action: mode === 'aprobar' ? 'aprobar' : null } });
      await refreshQuiz(payload, mode === 'aprobar' ? 'Pregunta editada y aprobada.' : 'Cambios guardados.');
    }
  } catch (err) { toast(err.message, 'error'); }
}

function useSuggestion(id, apply) {
  if (quizEditing !== id) { quizEditing = id; renderQuizEditor(); }
  apply(qs(`[data-quiz-form="${id}"]`));
}

document.addEventListener('click', e => {
  const t = e.target;
  const row = t.closest('[data-quiz-session]');
  if (row) return openQuiz(Number(row.dataset.quizSession));
  if (!currentQuiz) return;
  if (t.closest('[data-quiz-new]')) { quizEditing = 'new'; return renderQuizEditor(); }
  if (t.closest('[data-quiz-cancel]')) { quizEditing = null; return renderQuizEditor(); }
  const edit = t.closest('[data-quiz-edit]');
  if (edit) { quizEditing = Number(edit.dataset.quizEdit); return renderQuizEditor(); }
  const action = t.closest('[data-quiz-action]');
  if (action) return quizAction(Number(action.dataset.id), action.dataset.quizAction);
  const assist = t.closest('[data-quiz-assist]');
  if (assist) { assist.disabled = true; return quizAssist(assist.dataset.quizAssist, assist.dataset.id ? Number(assist.dataset.id) : null); }
  const distractor = t.closest('[data-use-distractor]');
  if (distractor) {
    const id = Number(distractor.dataset.id);
    const text = quizExtras[id].distractors[Number(distractor.dataset.useDistractor)];
    return useSuggestion(id, form => {
      const correct = Number(new FormData(form).get('answer_idx'));
      const used = quizExtras[id].used || [];
      const slot = [0, 1, 2, 3].find(i => i !== correct && !used.includes(i));
      if (slot === undefined) return;
      form.elements[`option${slot}`].value = text;
      quizExtras[id].used = [...used, slot];
    });
  }
  const suggestion = t.closest('[data-use-suggestion]');
  if (suggestion) {
    const id = Number(suggestion.dataset.id);
    return useSuggestion(id, form => { form.elements.question.value = quizExtras[id].review.suggestion; });
  }
});
document.addEventListener('submit', e => {
  const form = e.target.closest('[data-quiz-form]');
  if (!form) return;
  e.preventDefault();
  saveQuizForm(form, e.submitter?.dataset.quizSave || 'guardar');
});

// ---------- navegación ----------
function showView(name) {
  qsa('.sol-page-tabs button').forEach(b => { const on = b.dataset.view === name; b.classList.toggle('active', on); b.setAttribute('aria-selected', String(on)); });
  qsa('[id^="view-"]').forEach(v => v.classList.toggle('hidden', v.id !== `view-${name}`));
  if (name === 'cola') loadQueue();
  if (name === 'indicadores') loadCharts();
  if (name === 'tramos') loadKeySessions();
  if (name === 'quiz') loadQuizSessions();
}

document.addEventListener('click', e => {
  const t = e.target;
  const view = t.closest('[data-view]');
  if (view) return showView(view.dataset.view);
  const goto = t.closest('[data-goto]');
  if (goto) return showView(goto.dataset.goto);
  const counter = t.closest('[data-state]');
  if (counter) { stateFilter = stateFilter === counter.dataset.state ? null : counter.dataset.state; renderCounters(); renderRows(); showView('alumnos'); return; }
  const contact = t.closest('[data-contact]');
  if (contact) return contactModal(Number(contact.dataset.contact), contact.dataset.name);
  if (t.closest('[data-close-drawer]')) return closeDrawer();
  const user = t.closest('[data-user]');
  if (user) return openStudent(Number(user.dataset.user));
  const session = t.closest('[data-session]');
  if (session) return openKeys(Number(session.dataset.session));
  if (t.closest('#saveKeys')) return saveKeys();
  if (qs('#drawer').classList.contains('open') && !t.closest('#drawer, .sol-modal-backdrop')) closeDrawer();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeDrawer();
  if (e.key === 'Enter' && e.target.matches('tr[data-user], tr[data-session], tr[data-quiz-session]')) e.target.click();
});
document.addEventListener('change', e => {
  if (!currentKeys) return;
  if (e.target.matches('[data-key]')) { currentKeys.chapters[Number(e.target.dataset.key)].is_key = e.target.checked; renderKeySummary(); }
  if (e.target.matches('[data-logistics]')) {
    const c = currentKeys.chapters[Number(e.target.dataset.logistics)];
    c.logistics = e.target.checked;
    if (c.logistics) c.is_key = false;
    renderKeyEditor();
  }
  const bound = e.target.matches('[data-start]') ? 'start' : e.target.matches('[data-end]') ? 'end' : null;
  if (bound) {
    const c = currentKeys.chapters[Number(e.target.dataset[bound])];
    const value = parseTime(e.target.value);
    if (value === null) { e.target.value = fmtTime(c[bound]); toast('Escribe el minuto como mm:ss o h:mm:ss.', 'error'); return; }
    c[bound] = value;
    renderKeySummary();
  }
});
document.addEventListener('input', e => {
  if (e.target.matches('#search')) renderRows();
  if (currentKeys && e.target.matches('[data-title]')) currentKeys.chapters[Number(e.target.dataset.title)].title = e.target.value;
});

async function init() {
  courseId = await currentCourseId();
  await loadBoard();
  if (location.hash === '#cola') showView('cola');
}

init().catch(e => { qs('#studentRows').innerHTML = `<tr><td colspan="8" class="sol-empty">${esc(e.message)}</td></tr>`; });
