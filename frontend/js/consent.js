// Consentimiento de datos: modal al entrar (salvo «Más tarde» en esta pestaña) y aviso discreto en «Mi avance».
import { api, getUser, qs } from './api.js';

const laterKey = () => `dmc_consent_later_${getUser()?.id}`;
const accepted = new Set();

export const postponed = () => sessionStorage.getItem(laterKey()) === '1';

export function consentModal() {
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
  qs('[data-later]', wrap).addEventListener('click', () => {
    sessionStorage.setItem(laterKey(), '1');
    close();
  });
  qs('[data-accept]', wrap).addEventListener('click', async () => {
    await api('/consent', { method: 'POST', body: {} }).catch(() => {});
    sessionStorage.removeItem(laterKey());
    accepted.forEach(fn => fn());
    close();
  });
  qs('[data-accept]', wrap).focus();
}

export function consentBanner(root) {
  const box = document.createElement('p');
  box.className = 'sol-banner sol-consent-banner';
  box.innerHTML = `<i class="fa-solid fa-shield-halved"></i>
    <span>Aún no aceptas el uso de tus datos de asistencia. Sin eso no podemos avisarte a tiempo si te atrasas.</span>
    <button type="button" class="sol-btn sol-btn-sm sol-btn-ghost">Revisar y aceptar</button>`;
  qs('button', box).addEventListener('click', consentModal);
  accepted.add(() => box.remove());
  root.prepend(box);
}
