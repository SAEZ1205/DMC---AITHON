/* Pantalla de demostración: video + resumen persistido + chat con citas que saltan al minuto del video.
   Todo el texto del backend o del estudiante se muestra con textContent (jamás se interpreta como HTML). */
(function () {
  "use strict";

  // El backend se busca en localhost por defecto; ?api=http://127.0.0.1:8000 permite cambiarlo, pero solo a un origen local.
  const LOCAL_ORIGIN = /^http:\/\/(localhost|127\.0\.0\.1)(:\d{2,5})?$/;
  const requested = new URLSearchParams(location.search).get("api");
  const API = requested && LOCAL_ORIGIN.test(requested) ? requested : "http://127.0.0.1:8000";

  const STUDENT_ID = "demo-student-001";      // identificador SINTÉTICO de demostración (no es autenticación)
  const SUGGESTIONS = [
    "¿Qué es n8n?",
    "¿Cuál es la diferencia entre no-code y low-code?",
    "¿Qué procesos conviene automatizar?",
    "¿Qué diferencia existe entre un workflow y un RPA?",
    "¿Para qué sirve Docker en esta clase?",
  ];

  const $ = (id) => document.getElementById(id);
  const player = $("player");
  let lesson = null;            // { course_id, lesson_id, ... } del backend
  let sessionId = null;

  function fmt(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    const mm = String(m).padStart(h ? 2 : 1, "0"), rr = String(r).padStart(2, "0");
    return h ? `${h}:${mm}:${rr}` : `${mm}:${rr}`;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showError(message) {
    const panel = $("errorPanel");
    panel.textContent = message;
    panel.hidden = false;
  }

  async function api(path, options) {
    const response = await fetch(API + path, options);
    let body = null;
    try { body = await response.json(); } catch (_) { /* respuesta sin JSON */ }
    if (!response.ok) {
      const message = body && body.error ? body.error.message : `Error HTTP ${response.status}`;
      const error = new Error(message);
      error.code = body && body.error ? body.error.code : "http_error";
      throw error;
    }
    return body;
  }

  /* ---------------- navegación del video ---------------- */
  function seekTo(seconds) {
    if (!Number.isFinite(seconds)) return;
    const apply = () => {
      player.currentTime = Math.min(seconds, player.duration || seconds);
      player.play().catch(() => { /* el navegador puede exigir un gesto: el usuario ya hizo clic */ });
    };
    player.scrollIntoView({ behavior: "smooth", block: "center" });
    if (player.readyState >= 1) apply(); else player.addEventListener("loadedmetadata", apply, { once: true });
  }

  /* ---------------- clase y video ---------------- */
  async function loadLesson() {
    lesson = await api("/api/demo/lesson");
    $("lessonTitle").textContent = lesson.title;
    document.title = `${lesson.title} · RescuePath AI`;
    $("durationLabel").textContent = lesson.duration_label;
    $("modeBadge").textContent = `Modo ${lesson.mode}`;
    $("modeBadge").className = "demo-pill" + (lesson.mode === "LIVE" ? " live" : "");

    const v = lesson.video;
    $("videoInfo").textContent = v
      ? `${v.resolution} · ${v.video_codec}/${v.audio_codec}` + (v.browser_compatible_codecs ? " (compatible con navegadores)" : " (puede no reproducirse)")
      : "video no disponible";
    if (!lesson.video_available || !lesson.video_url) {
      $("videoError").textContent = "El video no está disponible en este equipo (revisa DEMO_JUEVES.md).";
      $("videoError").hidden = false;
    } else {
      player.src = API + lesson.video_url;
    }
    const t = lesson.transcript;
    $("statusLine").textContent =
      `Transcripción ingerida: ${t.segments} segmentos (${t.first_timestamp}–${t.last_timestamp}) · huella ${t.transcript_sha256_short}` +
      (lesson.warnings.length ? ` · ${lesson.warnings.length} aviso(s)` : "");
  }

  player.addEventListener("error", () => {
    const codes = { 1: "carga abortada", 2: "error de red", 3: "no se pudo decodificar", 4: "formato o códec no admitido" };
    const code = player.error ? player.error.code : 0;
    $("videoError").textContent = `El navegador no pudo reproducir el video (${codes[code] || "error desconocido"}). ` +
      "Prueba con Chrome o Edge actualizados; el archivo original no se modifica.";
    $("videoError").hidden = false;
  });

  /* ---------------- resumen persistido ---------------- */
  async function loadSummary() {
    const s = await api(`/api/courses/${lesson.course_id}/lessons/${lesson.lesson_id}/summary?engine=best`);
    const badges = $("summaryBadges");
    const engineLabel = s.engine === "kimi" ? `Generado con Kimi (${s.generated_by.replace("kimi:", "")})` : "Resumen heurístico DEMO (sin modelo de lenguaje)";
    badges.append(el("span", "demo-pill" + (s.engine === "kimi" ? " live" : " warn"), engineLabel));
    badges.append(el("span", "demo-pill", s.cache.cached ? "Reutilizado desde la base de datos" : "Generado ahora"));
    badges.append(el("span", "demo-pill", `Generado: ${new Date(s.cache.generated_at).toLocaleString()}`));
    $("summaryOverview").textContent = s.overview;

    const list = $("chapterList");
    list.replaceChildren();
    s.sections.forEach((sec) => {
      const ref = sec.references[0];
      const target = ref && ref.seek_seconds != null ? ref.seek_seconds : sec.start_seconds;
      const li = el("li");
      const btn = el("button", "demo-chapter");
      btn.type = "button";
      btn.title = `Ir a ${fmt(target)}`;
      btn.append(el("span", "t", `${fmt(sec.start_seconds)}`));
      const body = el("span");
      body.append(el("span", "title", sec.title), el("span", "point", sec.key_point));
      btn.append(body);
      btn.addEventListener("click", () => seekTo(target));
      li.append(btn);
      list.append(li);
    });
    $("summaryNote").textContent = s.method_note;
  }

  /* ---------------- chat ---------------- */
  function addMessage(role, text, className) {
    const box = el("div", `demo-msg ${role}${className ? " " + className : ""}`);
    box.append(el("div", "", text));
    $("messages").append(box);
    $("messages").scrollTop = $("messages").scrollHeight;
    return box;
  }

  function renderTutor(reply) {
    const oos = reply.response_type === "out_of_scope";
    const box = addMessage("tutor", reply.response, oos ? "oos" : "");

    if (reply.references.length) {
      const cites = el("div", "demo-cites");
      reply.references.forEach((ref) => {
        const target = ref.seek_seconds != null ? ref.seek_seconds : ref.start_seconds;
        const btn = el("button", "demo-cite", `▶ ${fmt(target)}`);
        btn.type = "button";
        btn.title = `Segmento citado: ${ref.timestamp}. Ir al minuto ${fmt(target)} del video.`;
        btn.addEventListener("click", () => seekTo(target));
        cites.append(btn);
      });
      box.append(cites);
    }

    const meta = el("div", "meta");
    meta.append(el("span", "demo-pill" + (reply.mode === "LIVE" ? " live" : ""), `Respuesta: ${reply.mode}`));
    if (reply.guardian) {
      const g = reply.guardian;
      const names = { relevant: "pertinente", not_relevant: "no pertinente", uncertain: "pertinencia incierta" };
      meta.append(el("span", "demo-pill" + (g.label === "not_relevant" ? " warn" : ""),
        `Guardián ${g.is_llm ? "LLM" : "heurístico (reglas)"}: ${names[g.label] || g.label}`));
    }
    box.append(meta);
    if (reply.provider_notice) box.append(el("div", "demo-note", reply.provider_notice));
    if (reply.response_type === "no_evidence") box.append(el("div", "demo-note", "Sin evidencia suficiente en la transcripción: no se inventan citas."));
    if (reply.telemetry_notice) $("telemetryNotice").textContent = reply.telemetry_notice;
  }

  async function ask(question) {
    const text = question.trim();
    if (!text) return;
    addMessage("user", text);
    const sendBtn = $("sendBtn");
    sendBtn.disabled = true;
    const pending = addMessage("tutor", "Buscando en la clase…");
    try {
      const reply = await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)) },
        body: JSON.stringify({
          student_id: STUDENT_ID, course_id: lesson.course_id, lesson_id: lesson.lesson_id, session_id: sessionId, message: text,
        }),
      });
      sessionId = reply.session_id;
      pending.remove();
      renderTutor(reply);
    } catch (error) {
      pending.remove();
      addMessage("tutor", `No pude responder: ${error.message}`, "oos");
    } finally {
      sendBtn.disabled = false;
    }
  }

  function setupChat() {
    const suggestions = $("suggestions");
    SUGGESTIONS.forEach((q) => {
      const b = el("button", "", q);
      b.type = "button";
      b.addEventListener("click", () => ask(q));
      suggestions.append(b);
    });
    $("chatForm").addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("question");
      const q = input.value;
      input.value = "";
      ask(q);
    });
    $("question").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("chatForm").requestSubmit(); }
    });
  }

  /* ---------------- arranque ---------------- */
  setupChat();
  (async () => {
    try {
      await loadLesson();
      await loadSummary();
    } catch (error) {
      $("statusLine").textContent = "No se pudo conectar con el asistente.";
      showError(error.code === "real_lesson_not_available"
        ? error.message
        : `No se pudo cargar la demostración: ${error.message}. ¿Está el backend en ${API}? (ver DEMO_JUEVES.md)`);
      $("summaryOverview").textContent = "Resumen no disponible.";
    }
  })();
})();
