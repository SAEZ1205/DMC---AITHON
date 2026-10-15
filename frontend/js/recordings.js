// Grabaciones: sesiones del curso con su estado de cumplimiento.
import { api, currentCourseId, esc, fmtDate, qs, requireUser, statusChip } from './api.js';

requireUser();
let sessions = [];
let filter = 'todas';

const FILTERS = [
  ['todas', 'Todas'], ['por_recuperar', 'Por recuperar'], ['cumplidas', 'Cumplidas'], ['no_cumplida', 'No cumplidas'],
];

function matches(s) {
  if (filter === 'todas') return s.status !== 'proxima';
  if (filter === 'cumplidas') return s.status === 'en_vivo' || s.status === 'recuperada';
  return s.status === filter;
}

function chip(s) {
  if (s.status === 'por_recuperar') return statusChip(s.status, `Por recuperar (vence ${s.deadline_short})`);
  if (s.status === 'recuperada') return statusChip(s.status, `Recuperada${s.completed_at ? ` el ${fmtDate(s.completed_at)}` : ''}`);
  return statusChip(s.status, s.status_label);
}

function render() {
  const pending = sessions.filter(s => s.status === 'por_recuperar').length;
  qs('#filters').innerHTML = FILTERS.map(([key, label]) =>
    `<button type="button" class="sol-filter${filter === key ? ' active' : ''}" data-filter="${key}">${label}${key === 'por_recuperar' && pending ? ` (${pending})` : ''}</button>`).join('');
  const rows = sessions.filter(matches).reverse();
  qs('#sessionList').innerHTML = rows.length ? rows.map(s => `
    <li class="sol-session-row${s.status === 'por_recuperar' ? ' pending' : ''}">
      <span class="sol-session-num">Sesión<strong>${s.number}</strong></span>
      <span class="sol-session-title">${esc(s.title)}<small>${fmtDate(s.starts_at, { weekday: 'long', day: 'numeric', month: 'long' })}</small></span>
      ${chip(s)}
      <a class="sol-btn ${s.status === 'por_recuperar' ? '' : 'sol-btn-ghost'} sol-btn-sm" href="recording.html?session=${s.id}">
        <i class="fa-solid fa-circle-play"></i> ${s.status === 'por_recuperar' ? 'Recuperar' : 'Ver clase'}</a>
    </li>`).join('') : '<li class="sol-empty">No hay sesiones con este filtro.</li>';
  const next = sessions.find(s => s.status === 'proxima');
  qs('#nextSession').innerHTML = next
    ? `<i class="fa-regular fa-calendar"></i> Próxima clase: sesión ${next.number}, ${fmtDate(next.starts_at, { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}.` : '';
}

async function init() {
  const courseId = await currentCourseId();
  sessions = await api(`/courses/${courseId}/sessions`);
  if (sessions.some(s => s.status === 'por_recuperar')) filter = 'por_recuperar';
  render();
}

document.addEventListener('click', e => {
  const f = e.target.closest('[data-filter]');
  if (f) { filter = f.dataset.filter; render(); qs(`[data-filter="${filter}"]`)?.focus(); }
});

init().catch(e => { qs('#sessionList').innerHTML = `<li class="sol-empty">${esc(e.message)}</li>`; });
