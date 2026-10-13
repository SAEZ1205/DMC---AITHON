// Se carga en todas las páginas: agrega los accesos nuevos al sidebar, la campana, el consentimiento
// y el seguimiento de tiempo activo, sin cambiar la maqueta existente.
import { api, currentCourseId, getMe, getUser, isStaff, qs, qsa, setUser } from './api.js';
import { initNotifications } from './notifications.js';
import { startTracker } from './tracker.js';

const page = location.pathname.split('/').pop() || 'index.html';

function sideItem(href, icon, text) {
  const li = document.createElement('li');
  li.innerHTML = `<a class="side-item${page === href ? ' active' : ''}" href="${href}"><i class="${icon}"></i><span class="text">${text}</span></a>`;
  return li;
}

function enhanceSidebar(user) {
  const personal = qsa('#sidebar .side-item').find(a => a.getAttribute('href') === 'dashboard.html')?.closest('li');
  if (personal && !qs('#sidebar [data-sol]')) {
    const item = isStaff(user)
      ? sideItem('tutor.html', 'fa-solid fa-user-group', 'Seguimiento')
      : sideItem('progress.html', 'fa-solid fa-chart-line', 'Mi avance');
    item.dataset.sol = '1';
    personal.after(item);
  }
  qsa('#sidebar .course-submenu a[href="course-detail.html#grabaciones"]').forEach(a => {
    a.href = 'recordings.html';
    if (page === 'recordings.html' || page === 'recording.html') a.classList.add('active');
  });
}

function enhanceProfile(user) {
  const name = qs('#profileMenu .profile-name span');
  if (name) name.textContent = user.name.length > 12 ? `${user.name.slice(0, 10).toUpperCase()}...` : user.name.toUpperCase();
  qsa('#profileMenu a[href="index.html"]').forEach(a => a.addEventListener('click', () => setUser(null)));
}

function consentModal() {
  const wrap = document.createElement('div');
  wrap.className = 'sol-modal-backdrop';
  wrap.innerHTML = `<div class="sol-modal" role="dialog" aria-modal="true" aria-labelledby="consentTitle">
    <h2 id="consentTitle"><i class="fa-solid fa-shield-halved"></i> Tus datos te ayudan a certificarte</h2>
    <p>Para acompañarte durante el curso, DMC Institute registra:</p>
    <ul>
      <li>Tu asistencia y participación en las clases de Zoom (entradas, salidas, chat e intervenciones).</li>
      <li>Tu tiempo activo en la plataforma y el avance de las grabaciones que ves.</li>
      <li>Las preguntas que haces al chat de la clase y tus resultados en los quizzes.</li>
    </ul>
    <p>Con esto calculamos tu asistencia efectiva y, si notamos que te estás atrasando, te avisamos a tiempo o te contacta tu Asistente Académico.
    Solo tú y el equipo académico del curso ven esta información (Ley N.° 29733 de Protección de Datos Personales).</p>
    <div class="sol-modal-actions">
      <button type="button" class="sol-btn sol-btn-ghost" data-later>Más tarde</button>
      <button type="button" class="sol-btn" data-accept>Acepto</button>
    </div></div>`;
  document.body.append(wrap);
  const close = () => wrap.remove();
  qs('[data-later]', wrap).addEventListener('click', close);
  qs('[data-accept]', wrap).addEventListener('click', async () => {
    await api('/consent', { method: 'POST', body: {} }).catch(() => {});
    close();
  });
  qs('[data-accept]', wrap).focus();
}

function mobileDrawer() {
  if (!document.body.classList.contains('sol-responsive')) return;
  const mobile = matchMedia('(max-width: 900px)');
  qs('#menuToggle')?.addEventListener('click', () => {
    if (mobile.matches) document.body.classList.toggle('sol-nav-open');
  });
  document.addEventListener('click', e => {
    if (mobile.matches && !e.target.closest('#sidebar, #menuToggle')) document.body.classList.remove('sol-nav-open');
  });
}

async function init() {
  mobileDrawer();
  const user = getUser();
  if (!user) return;
  enhanceSidebar(user);
  enhanceProfile(user);
  if (isStaff(user)) return;
  initNotifications();
  try {
    const me = await getMe();
    if (!me.consent) consentModal();
    startTracker(await currentCourseId());
  } catch { /* la página funciona igual sin API */ }
}

init();
