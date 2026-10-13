// Página del curso: asistencia efectiva con marca del umbral y baja asistida.
import { api, currentCourseId, esc, getUser, isStaff, pct, qs, qsa } from './api.js';

const REASONS = [
  ['tiempo', 'Falta de tiempo'], ['trabajo', 'Trabajo / cambio de horario'], ['contenido', 'El contenido'],
  ['economico', 'Motivos económicos'], ['salud', 'Salud'], ['otro', 'Otro'],
];
const OFFERS = [
  ['recuperar', 'fa-solid fa-circle-play', 'Recuperar las clases con las grabaciones', 'Cada clase se recupera en unos 20 minutos viendo sus tramos clave y aprobando el quiz.'],
  ['pausa', 'fa-solid fa-pause', 'Pausar y retomar en otra cohorte', 'Conservas lo avanzado y continúas cuando tengas tiempo.'],
  ['hablar_asistente', 'fa-regular fa-comments', 'Hablar con mi Asistente Académico', 'Te contacta para buscar juntos una alternativa.'],
];

async function renderAttendance(courseId) {
  const box = qs('#attendanceBox');
  if (!box) return;
  const p = await api(`/courses/${courseId}/progress`);
  qs('#attendanceLabel').textContent = `Asistencia efectiva ${pct(p.effective)}`;
  const fill = qs('#attendanceFill');
  fill.style.width = `${Math.min(100, p.effective * 100)}%`;
  fill.classList.toggle('sol-fill-ok', p.effective >= p.threshold);
  fill.classList.toggle('sol-fill-warn', p.effective < p.threshold);
  qs('#attendanceMark').style.left = `${p.threshold * 100}%`;
  qs('#attendanceMark').dataset.label = `Meta ${pct(p.threshold)}`;
  qs('#attendanceDetail').innerHTML = `${p.complied} de ${p.dictated} sesiones dictadas cumplidas (en vivo o recuperadas)
    · Proyección al cierre <strong class="${p.projection < p.threshold ? 'sol-text-warn' : ''}">${pct(p.projection)}</strong>
    ${p.pending ? `· <a href="recordings.html">${p.pending} por recuperar</a>` : ''}`;
}

function withdrawalModal(courseId) {
  const wrap = document.createElement('div');
  wrap.className = 'sol-modal-backdrop';
  wrap.innerHTML = `<div class="sol-modal" role="dialog" aria-modal="true" aria-labelledby="wdTitle">
    <h2 id="wdTitle"><i class="fa-solid fa-door-open"></i> Solicitar retiro del curso</h2>
    <form id="wdStep1">
      <label>¿Cuál es el motivo principal?</label>
      <div class="sol-options">${REASONS.map(([k, l], i) => `<label><input type="radio" name="reason" value="${k}" ${i === 0 ? 'required' : ''}><span>${l}</span></label>`).join('')}</div>
      <label for="wdComment">Comentario (opcional)</label>
      <textarea id="wdComment" rows="3" maxlength="2000" placeholder="Cuéntanos qué está pasando"></textarea>
      <div class="sol-modal-actions"><button type="button" class="sol-btn sol-btn-ghost" data-close>Cancelar</button><button class="sol-btn">Continuar</button></div>
    </form>
    <div id="wdStep2" class="hidden">
      <p>Antes de confirmar, ¿te sirve alguna de estas opciones?</p>
      <div class="sol-offer">${OFFERS.map(([k, icon, title, desc]) => `<button type="button" data-choice="${k}"><i class="${icon}"></i><span><strong>${title}</strong><br><small class="sol-muted">${desc}</small></span></button>`).join('')}</div>
      <div class="sol-modal-actions"><button type="button" class="sol-btn sol-btn-ghost" data-close>Cancelar</button>
        <button type="button" class="sol-btn sol-btn-danger" data-choice="confirmar_retiro">Confirmar solicitud de retiro</button></div>
    </div>
    <div id="wdStep3" class="hidden" role="status"></div>
  </div>`;
  document.body.append(wrap);
  const close = () => wrap.remove();
  qsa('[data-close]', wrap).forEach(b => b.addEventListener('click', close));
  wrap.addEventListener('click', e => { if (e.target === wrap) close(); });
  document.addEventListener('keydown', function onKey(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } });
  qs('input[name=reason]', wrap).focus();

  qs('#wdStep1', wrap).addEventListener('submit', e => {
    e.preventDefault();
    qs('#wdStep1', wrap).classList.add('hidden');
    qs('#wdStep2', wrap).classList.remove('hidden');
  });
  qsa('[data-choice]', wrap).forEach(btn => btn.addEventListener('click', async () => {
    const body = {
      reason: qs('input[name=reason]:checked', wrap).value,
      comment: qs('#wdComment', wrap).value.trim() || null,
      choice: btn.dataset.choice,
    };
    qsa('[data-choice]', wrap).forEach(b => { b.disabled = true; });
    try {
      const res = await api(`/courses/${courseId}/withdrawal`, { method: 'POST', body });
      qs('#wdStep2', wrap).classList.add('hidden');
      const step3 = qs('#wdStep3', wrap);
      step3.classList.remove('hidden');
      step3.innerHTML = `<p class="sol-banner sol-banner-ok"><i class="fa-solid fa-circle-check"></i><span>${esc(res.message)}</span></p>
        <div class="sol-modal-actions">${body.choice === 'recuperar' ? '<a class="sol-btn" href="recordings.html">Ir a mis grabaciones</a>' : ''}
        <button type="button" class="sol-btn sol-btn-ghost" data-close>Cerrar</button></div>`;
      qs('[data-close]', step3).addEventListener('click', close);
    } catch (err) {
      qsa('[data-choice]', wrap).forEach(b => { b.disabled = false; });
      alert(err.message);
    }
  }));
}

async function init() {
  const user = getUser();
  if (!user || isStaff(user)) { qs('#withdrawWrap')?.classList.add('hidden'); return; }
  const courseId = await currentCourseId();
  qs('#withdrawBtn')?.addEventListener('click', () => withdrawalModal(courseId));
  await renderAttendance(courseId);
}

init().catch(() => {});
