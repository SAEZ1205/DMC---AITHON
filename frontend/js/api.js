// Cliente de la API y utilidades compartidas por las páginas nuevas.
const USER_KEY = 'dmcUser';
export const STAFF_ROLES = ['tutor', 'asistente_academico', 'docente', 'coordinador'];

export function getUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
}

export function setUser(user) {
  if (user) localStorage.setItem(USER_KEY, JSON.stringify({ id: user.id, name: user.name, role: user.role }));
  else localStorage.removeItem(USER_KEY);
}

export const isStaff = (user = getUser()) => !!user && STAFF_ROLES.includes(user.role);

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function api(path, { method = 'GET', body } = {}) {
  const user = getUser();
  const headers = { Accept: 'application/json' };
  if (user) headers['X-User-Id'] = String(user.id);
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(`/api${path}`, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  const data = res.headers.get('content-type')?.includes('json') ? await res.json() : null;
  if (!res.ok) {
    if (res.status === 401) { setUser(null); location.href = 'index.html'; }
    const detail = typeof data?.detail === 'string' ? data.detail : 'No se pudo completar la acción.';
    throw new ApiError(res.status, detail);
  }
  return data;
}

// Envío al salir de la página (sendBeacon no admite cabeceras: el usuario va en ?uid=).
export function beacon(path, body) {
  const user = getUser();
  if (!user) return;
  const blob = new Blob([JSON.stringify(body)], { type: 'application/json' });
  navigator.sendBeacon(`/api${path}${path.includes('?') ? '&' : '?'}uid=${user.id}`, blob);
}

export function requireUser({ staff = false } = {}) {
  const user = getUser();
  if (!user) { location.href = 'index.html'; throw new Error('sin sesión'); }
  if (staff && !isStaff(user)) { location.href = 'course-detail.html'; throw new Error('sin permiso'); }
  return user;
}

let mePromise;
export const getMe = () => (mePromise ??= api('/me'));

export async function currentCourseId() {
  const param = new URLSearchParams(location.search).get('course');
  if (param) return Number(param);
  const me = await getMe();
  return me.courses[0]?.id;
}

export const qs = (sel, root = document) => root.querySelector(sel);
export const qsa = (sel, root = document) => [...root.querySelectorAll(sel)];

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function fmtTime(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const pad = n => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(r)}` : `${pad(m)}:${pad(r)}`;
}

export const pct = (value, digits = 0) => value == null ? '—' : `${(value * 100).toFixed(digits)} %`;

export function fmtDate(iso, opts = { day: '2-digit', month: '2-digit' }) {
  return iso ? new Date(iso).toLocaleDateString('es-PE', opts) : '—';
}

export function fmtDateTime(iso) {
  return iso ? new Date(iso).toLocaleString('es-PE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
}

export function relativeDays(iso, nowIso) {
  if (!iso) return '—';
  const days = Math.floor((new Date(nowIso) - new Date(iso)) / 86400000);
  if (days <= 0) return 'hoy';
  if (days === 1) return 'ayer';
  return `hace ${days} días`;
}

// Convierte [mm:ss] / [h:mm:ss] en enlaces que saltan en el video.
const TS = /\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/g;
export function tsToSeconds(a, b, c) {
  return c !== undefined ? Number(a) * 3600 + Number(b) * 60 + Number(c) : Number(a) * 60 + Number(b);
}
// «1 sesión», «2 sesiones»; sin `many` agrega «s».
export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

export function linkTimestamps(html) {
  return html.replace(TS, (m, a, b, c) => `<button type="button" class="sol-ts" data-seek="${tsToSeconds(a, b, c)}">${m.slice(1, -1)}</button>`);
}

// Markdown mínimo (títulos, negritas, listas y párrafos) para el resumen y el chat.
export function renderMarkdown(text) {
  const lines = esc(text).split(/\r?\n/);
  const out = [];
  let list = null, para = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${para.join(' ')}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<ul>${list.map(i => `<li>${i}</li>`).join('')}</ul>`); list = null; } };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushPara(); flushList(); continue; }
    const heading = line.match(/^#{1,4}\s+(.*)/);
    const item = line.match(/^[-*•]\s+(.*)/) || line.match(/^\d+[.)]\s+(.*)/);
    if (heading) { flushPara(); flushList(); out.push(`<h4>${heading[1]}</h4>`); }
    else if (item) { flushPara(); (list ??= []).push(item[1]); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList();
  return linkTimestamps(out.join('').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'));
}

export function toast(message, tone = 'info') {
  let box = qs('.sol-toasts');
  if (!box) { box = document.createElement('div'); box.className = 'sol-toasts'; document.body.append(box); }
  const el = document.createElement('div');
  el.className = `sol-toast sol-toast-${tone}`;
  el.setAttribute('role', 'status');
  el.textContent = message;
  box.append(el);
  setTimeout(() => el.remove(), 4500);
}

export const STATE_ICONS = {
  en_vivo: 'fa-solid fa-circle-check', recuperada: 'fa-solid fa-circle-check',
  por_recuperar: 'fa-solid fa-hourglass-half', no_cumplida: 'fa-solid fa-circle-xmark', proxima: 'fa-regular fa-clock',
};

export function statusChip(status, label, extra = '') {
  return `<span class="sol-chip sol-chip-${status}"><i class="${STATE_ICONS[status] || 'fa-solid fa-circle'}"></i>${esc(label)}${extra}</span>`;
}
