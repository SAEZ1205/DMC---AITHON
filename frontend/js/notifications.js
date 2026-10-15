// Campana de notificaciones (.top-icon existente): mensajes de nivel 1 de la bandeja de salida.
import { api, esc, fmtDateTime, qs } from './api.js';

export async function initNotifications() {
  const bell = qs('.top-icon[aria-label="Notificaciones"]');
  if (!bell) return;
  bell.classList.add('sol-bell');
  const panel = document.createElement('div');
  panel.className = 'sol-notif-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Notificaciones');
  bell.after(panel);
  const badge = document.createElement('span');
  badge.className = 'sol-badge hidden';
  bell.append(badge);

  let data = { unread: 0, items: [] };
  const render = () => {
    badge.textContent = data.unread;
    badge.classList.toggle('hidden', !data.unread);
    panel.innerHTML = `<div class="sol-notif-head">Notificaciones</div>` + (data.items.length
      ? data.items.map(n => `<a class="sol-notif-item${n.read ? '' : ' unread'}" href="progress.html">
          <i class="fa-solid fa-circle-exclamation"></i><span><span class="sol-notif-text">${esc(n.text)}</span>
          <small>${fmtDateTime(n.at)}</small></span></a>`).join('')
      : `<p class="sol-notif-empty">No tienes notificaciones.</p>`);
  };
  const load = async () => { try { data = await api('/me/notifications'); render(); } catch { /* sin sesión */ } };

  bell.addEventListener('click', async e => {
    e.stopPropagation();
    const open = !panel.classList.contains('open');
    panel.classList.toggle('open', open);
    if (open && data.unread) {
      await api('/me/notifications/read', { method: 'POST' }).catch(() => {});
      data.unread = 0;
      badge.classList.add('hidden');
    }
  });
  document.addEventListener('click', e => { if (!e.target.closest('.sol-notif-panel')) panel.classList.remove('open'); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') panel.classList.remove('open'); });
  await load();
}
