// Accesos de demostración en el login: guardan el rol y el user_id en localStorage.
import { api, esc, qs, setUser } from './api.js';

const box = qs('.login-instructions');
if (box) {
  const demo = document.createElement('div');
  demo.className = 'sol-demo-login';
  demo.innerHTML = `
    <p><i class="fa-solid fa-flask"></i> Demo del MVP</p>
    <div class="sol-demo-buttons">
      <button type="button" class="guest-btn" data-role="alumno"><i class="fa-solid fa-user-graduate"></i> Entrar como alumno</button>
      <button type="button" class="guest-btn" data-role="tutor"><i class="fa-solid fa-user-tie"></i> Entrar como tutor</button>
    </div>
    <details class="sol-demo-more"><summary>Elegir otro usuario</summary>
      <select aria-label="Usuario de demostración"><option value="">Cargando…</option></select>
      <button type="button" class="guest-btn" data-pick>Entrar</button>
    </details>
    <p class="sol-demo-error hidden" role="alert"></p>`;
  box.append(demo);
  const error = qs('.sol-demo-error', demo);

  const enter = async body => {
    error.classList.add('hidden');
    try {
      const user = await api('/auth/demo', { method: 'POST', body });
      setUser(user);
      location.href = user.role === 'alumno' ? 'course-detail.html' : 'tutor.html';
    } catch (e) {
      error.textContent = `${e.message}. ¿Ejecutaste python scripts/seed_demo.py y uvicorn desde backend/?`;
      error.classList.remove('hidden');
    }
  };
  demo.querySelectorAll('[data-role]').forEach(b => b.addEventListener('click', () => enter({ role: b.dataset.role })));

  const select = qs('select', demo);
  qs('details', demo).addEventListener('toggle', async () => {
    if (select.dataset.loaded) return;
    try {
      const users = await api('/auth/demo-users');
      const label = { estable: 'estable', cambio_horario: 'cambio de horario', adaptado: 'adaptado', mala_conexion: 'mala conexión', desinteres: 'desinterés' };
      select.innerHTML = `<optgroup label="Equipo académico">${users.staff.map(u => `<option value="${u.id}" data-role="tutor">${esc(u.name)} (${esc(u.role.replace('_', ' '))})</option>`).join('')}</optgroup>
        <optgroup label="Alumnos (perfil sintético)">${users.students.map(u => `<option value="${u.id}" data-role="alumno">${esc(u.name)}${u.profile ? ` · ${label[u.profile] || u.profile}` : ''}</option>`).join('')}</optgroup>`;
      select.dataset.loaded = '1';
    } catch (e) { select.innerHTML = `<option value="">${esc(e.message)}</option>`; }
  });
  qs('[data-pick]', demo).addEventListener('click', () => {
    const opt = select.selectedOptions[0];
    if (opt?.value) enter({ role: opt.dataset.role, user_id: Number(opt.value) });
  });
}
