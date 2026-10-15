// Tiempo activo en la plataforma: un heartbeat cada 30 s solo si la pestaña está visible
// y hubo interacción (o un video reproduciéndose) en los últimos 60 s.
import { api, getUser } from './api.js';

const EVERY_MS = 30_000;
const IDLE_MS = 60_000;
let lastInteraction = Date.now();
let lastBeat = Date.now();
let started = false;

export function markActive() { lastInteraction = Date.now(); }

export function startTracker(courseId) {
  if (started || !courseId || !getUser()) return;
  started = true;
  ['mousemove', 'keydown', 'scroll', 'click', 'touchstart'].forEach(evt =>
    window.addEventListener(evt, markActive, { passive: true }));
  setInterval(() => {
    const now = Date.now();
    const active = document.visibilityState === 'visible' && now - lastInteraction <= IDLE_MS;
    const elapsed = Math.round((now - lastBeat) / 1000);
    lastBeat = now;
    if (!active) return;
    const page = location.pathname.split('/').pop() || 'index.html';
    api('/heartbeat', { method: 'POST', body: { course_id: courseId, page, active_seconds: Math.min(elapsed, 60) } })
      .catch(() => {});
  }, EVERY_MS);
}
