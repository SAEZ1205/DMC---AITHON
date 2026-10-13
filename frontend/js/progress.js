// Mi avance: asistencia efectiva frente al umbral, proyección, faltantes, clases por recuperar,
// notas de quiz y requisitos del certificado.
import { api, currentCourseId, esc, pct, qs, requireUser } from './api.js';

requireUser();

const REQ = {
  cumplido: ['fa-solid fa-circle-check', 'ok', 'Cumplido'],
  en_camino: ['fa-solid fa-circle-check', 'ok', 'Vas en camino'],
  en_riesgo: ['fa-solid fa-triangle-exclamation', 'pending', 'En riesgo'],
  no_cumplido: ['fa-solid fa-circle-xmark', 'bad', 'No cumplido'],
  pendiente: ['fa-regular fa-clock', 'pending', 'Pendiente'],
  pendiente_verificar: ['fa-regular fa-circle-question', 'unknown', 'Pendiente de verificar'],
};

function render(p) {
  qs('#courseName').textContent = p.course.name;
  const thr = p.threshold;
  const needed = Math.ceil(thr * p.total - 1e-9);
  const below = p.projection < thr;
  const recovery = p.course.recovery_days;
  const days = n => `${n} día${n === 1 ? '' : 's'}`;

  const pendingHtml = p.pending_items.length ? p.pending_items.map(c => `
    <li><span><strong>Sesión ${c.number}</strong> – ${esc(c.title)}
      <small>Vence el ${esc(c.deadline_text)}${c.days_left != null ? ` (${c.days_left ? `quedan ${days(c.days_left)}` : 'vence hoy'})` : ''}
      · tramos clave ${pct(c.key_coverage)} · quiz ${c.best_quiz == null ? 'sin intentos' : `${c.best_quiz}/5`}</small></span>
      <a class="sol-btn sol-btn-sm" href="recording.html?session=${c.session_id}"><i class="fa-solid fa-circle-play"></i> Recuperar</a></li>`).join('')
    : '<li><span class="sol-muted">No tienes clases por recuperar. ¡Bien!</span></li>';

  const quizzes = p.quizzes.slice(-8).reverse();
  const quizHtml = quizzes.length ? quizzes.map(q => `
    <li><span>Sesión ${q.number} – ${esc(q.title)}</span>
      <span class="sol-chip sol-chip-${q.best >= p.course.quiz_pass_score ? 'en_vivo' : 'por_recuperar'}">${q.best}/5</span></li>`).join('')
    : '<li><span class="sol-muted">Aún no rindes quizzes.</span></li>';

  const reqHtml = p.certification.requirements.map(r => {
    const [icon, tone, label] = REQ[r.state] || REQ.pendiente;
    return `<li class="sol-req"><span><i class="${icon} ${tone}"></i> ${esc(r.label)}${r.value != null ? ` (${r.value})` : ''}</span><small class="${tone}">${label}</small></li>`;
  }).join('');

  qs('#progressRoot').innerHTML = `
    <div class="sol-kpis">
      <div class="sol-kpi ${p.effective >= thr ? 'ok' : 'warn'}"><span>Asistencia efectiva</span><strong>${pct(p.effective)}</strong>
        <small>${p.complied} de ${p.dictated} sesiones dictadas</small></div>
      <div class="sol-kpi ${below ? 'bad' : 'ok'}"><span>Proyección al cierre</span><strong>${pct(p.projection)}</strong>
        <small>${below ? `menor que el ${pct(thr)} requerido` : `sobre el ${pct(thr)} requerido`}</small></div>
      <div class="sol-kpi"><span>Te faltan</span><strong>${p.missing}</strong>
        <small>sesiones para el ${pct(thr)} (quedan ${p.remaining_sessions})</small></div>
      <div class="sol-kpi ${p.pending ? 'warn' : 'ok'}"><span>Por recuperar</span><strong>${p.pending}</strong>
        <small>${p.pending_items[0] ? `la primera vence el ${esc(p.pending_items[0].deadline_short)}` : 'al día'}</small></div>
    </div>
    <section class="content-card">
      <h2 class="sol-card-title">¿Cuánto me falta para certificarme?</h2>
      <div class="sol-big-bar">
        <div class="sol-bar">
          <span class="sol-projection" style="left:0;width:${Math.min(100, p.projection * 100)}%"></span>
          <div class="sol-bar-fill ${p.complied / p.total >= thr ? 'ok' : ''}" style="position:relative;width:${(p.complied / p.total) * 100}%"></div>
          <span class="sol-bar-mark" style="left:${thr * 100}%" data-label="Meta ${pct(thr)}"></span>
        </div>
        <div class="sol-legend">
          <span><i style="background:var(--blue)"></i>Cumplidas: ${p.complied} de ${p.total} sesiones del programa</span>
          <span><i style="background:rgba(42,123,183,.25)"></i>Proyección a tu ritmo actual: ${pct(p.projection)}</span>
          <span><i style="background:#455a64;width:3px"></i>Necesitas ${needed} sesiones cumplidas</span>
        </div>
      </div>
      <p class="${below ? 'sol-banner sol-banner-warn' : 'sol-banner sol-banner-ok'}">
        <i class="fa-solid ${below ? 'fa-triangle-exclamation' : 'fa-circle-check'}"></i>
        <span>${below
          ? `A tu ritmo de las últimas 4 sesiones terminarías con ${pct(p.projection)}, por debajo del ${pct(thr)}. ${p.pending ? `Recupera tus ${p.pending} clase${p.pending === 1 ? '' : 's'} pendiente${p.pending === 1 ? '' : 's'}: cada una toma unos 20 minutos.` : 'Asiste en vivo o recupera cada clase dentro de los 7 días.'}`
          : `Vas bien: a tu ritmo actual terminarías con ${pct(p.projection)}. Si no puedes asistir en vivo, recupera la clase dentro de ${recovery} días.`}</span>
      </p>
      <p class="sol-note">En vivo: ${p.live} · Recuperadas: ${p.recovered} · Por recuperar: ${p.pending} · No cumplidas: ${p.failed}</p>
    </section>
    <div class="sol-grid-2">
      <section class="content-card">
        <h2 class="sol-card-title"><i class="fa-solid fa-hourglass-half"></i> Clases por recuperar</h2>
        <ul class="sol-list">${pendingHtml}</ul>
        <p class="sol-note">Para recuperar: ve los tramos clave (${pct(p.course.key_coverage_min)}) en la vista de clase y aprueba el quiz (${p.course.quiz_pass_score} de 5) antes del plazo.</p>
      </section>
      <section class="content-card">
        <h2 class="sol-card-title"><i class="fa-solid fa-award"></i> Requisitos del certificado</h2>
        <ul class="sol-list">${reqHtml}</ul>
        ${p.course.academic_assistant ? `<p class="sol-help"><i class="fa-regular fa-life-ring"></i> ¿Necesitas ayuda para ponerte al día? Escribe a tu Asistente Académico, ${esc(p.course.academic_assistant)}.</p>` : ''}
      </section>
    </div>
    <section class="content-card">
      <h2 class="sol-card-title"><i class="fa-solid fa-clipboard-check"></i> Notas de quiz recientes</h2>
      <ul class="sol-list">${quizHtml}</ul>
      <p class="sol-note"><a href="recordings.html">Ver todas las sesiones <i class="fa-solid fa-arrow-right"></i></a></p>
    </section>`;
}

async function init() {
  const courseId = await currentCourseId();
  render(await api(`/courses/${courseId}/progress`));
}

init().catch(e => { qs('#progressRoot').innerHTML = `<p class="sol-empty">${esc(e.message)}</p>`; });
