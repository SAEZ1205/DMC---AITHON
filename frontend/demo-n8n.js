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
  let busy = false;             // hay una pregunta en curso: no se aceptan envíos duplicados

  // El backend limita la respuesta de Kimi a ~150 s por pregunta (ver la guía del jueves). El navegador espera un poco más que él y,
  // si aun así no hubo respuesta, cancela la solicitud en lugar de esperar para siempre.
  const CHAT_TIMEOUT_MS = 190000;
  // Streaming (SSE): OPCIONAL y solo si se abre la página con ?stream=1 y el backend lo tiene activado; si no, se usa el chat normal.
  const STREAMING = new URLSearchParams(location.search).get("stream") === "1";
  const SLOW_HINT_MS = 15000;

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

  window.RescueDemo = { seekTo };       // lo usa la pestaña «Validar aprendizaje» para llevar al minuto de la clase

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
    const byKimi = s.engine === "kimi";
    const engineLabel = byKimi ? `Resumen generado con Kimi · ${s.generated_by}` : "Resumen local por temas · sin IA generativa";
    badges.append(el("span", "demo-pill" + (byKimi ? " live" : " warn"), engineLabel));
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

  function renderTutor(reply, perceived) {
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
    const live = reply.mode === "LIVE";
    const cached = reply.mode === "CACHED";                  // guardada: verificada antes; NO es una generación nueva de Kimi
    const label = live ? `Respuesta: LIVE · ${reply.provider}${reply.model ? " · " + reply.model : ""}`
      : cached ? `Respuesta guardada · generada antes por Kimi${reply.cached_at ? " (" + reply.cached_at.slice(0, 10) + ")" : ""}`
      : (reply.fallback_used ? "Respuesta DEMO · NO la generó Kimi" : "Respuesta: DEMO");
    meta.append(el("span", "demo-pill" + (live ? " live" : (cached ? " cached" : (reply.fallback_used ? " warn" : ""))), label));
    const timing = reply.timing;                              // solo números del backend; lo que espera el estudiante lo mide el navegador
    if (timing && Number.isFinite(timing.total_ms)) {
      const parts = [`servidor ${(timing.total_ms / 1000).toFixed(1)} s`];
      if (Number.isFinite(timing.provider_ms)) parts.push(`Kimi ${(timing.provider_ms / 1000).toFixed(1)} s`);
      if (perceived && Number.isFinite(perceived.firstTextMs)) parts.push(`Primer texto ${(perceived.firstTextMs / 1000).toFixed(1)} s`);
      if (perceived && Number.isFinite(perceived.waitMs)) parts.push(`espera total ${(perceived.waitMs / 1000).toFixed(1)} s`);
      const pill = el("span", "demo-pill", "⏱ " + parts.join(" · "));
      pill.title = "Tiempo del servidor, de Kimi y lo que esperaste en el navegador. Con streaming, «Primer texto» es cuándo viste las primeras palabras (un borrador).";
      meta.append(pill);
    }
    if (reply.fallback_used) box.classList.add("fallback");
    if (reply.guardian) {
      const g = reply.guardian;
      const names = { relevant: "pertinente", not_relevant: "no pertinente", uncertain: "pertinencia incierta" };
      meta.append(el("span", "demo-pill" + (g.label === "not_relevant" ? " warn" : ""),
        `Guardián ${g.is_llm ? "LLM" : "heurístico (reglas)"}: ${names[g.label] || g.label}`));
    }
    box.append(meta);
    if (reply.provider_notice) box.append(el("div", "demo-note warn", reply.provider_notice));
    if (reply.response_type === "no_evidence") box.append(el("div", "demo-note", "Sin evidencia suficiente en la transcripción: no se inventan citas."));
    if (reply.telemetry_notice) $("telemetryNotice").textContent = reply.telemetry_notice;
  }

  /* Estado de espera: mientras Kimi razona (puede tardar más de un minuto) se muestra qué ocurre y cuánto lleva. */
  function startPending(live) {
    const box = el("div", "demo-msg tutor pending");
    box.setAttribute("role", "status");
    const row = el("div", "demo-pending-row");
    const clock = el("span", "demo-pending-clock", "0 s");
    clock.setAttribute("aria-hidden", "true");           // el contador no se anuncia cada segundo a los lectores de pantalla
    row.append(el("span", "demo-spinner"), el("span", "demo-pending-text", live ? "Kimi está preparando tu respuesta…" : "Buscando en la clase…"), clock);
    const hint = el("div", "demo-note", "");
    box.append(row, hint);
    $("messages").append(box);
    $("messages").scrollTop = $("messages").scrollHeight;
    const started = Date.now();
    const timer = setInterval(() => {
      const elapsed = Date.now() - started;
      clock.textContent = `${Math.floor(elapsed / 1000)} s`;
      if (live && elapsed >= SLOW_HINT_MS) {
        hint.textContent = "Kimi razona antes de responder y puede tardar hasta 2 minutos. No hace falta volver a enviar la pregunta.";
      }
    }, 1000);
    let draft = null;
    return {
      /* Texto PROVISIONAL del streaming: se rotula como borrador y se sustituye por la respuesta verificada al terminar. */
      appendDraft(text) {
        if (!draft) {
          const label = el("div", "demo-note", "Borrador en curso: se verificará al terminar y las citas aparecerán al final.");
          draft = el("div", "demo-draft", "");
          box.append(label, draft);
          hint.textContent = "";
        }
        draft.textContent += text;                            // textContent: nunca se interpreta como HTML
        $("messages").scrollTop = $("messages").scrollHeight;
      },
      stop() { clearInterval(timer); box.remove(); },
    };
  }

  /* Lee un flujo SSE del backend y entrega cada evento {name, data}. Los comentarios (": keep-alive") se ignoran. */
  async function readSse(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        let name = "message", data = "";
        frame.split("\n").forEach((line) => {
          if (line.startsWith("event:")) name = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        });
        if (data) onEvent({ name, data: JSON.parse(data) });
      }
    }
  }

  async function streamChat(body, controller, pending, perceived, started) {
    const response = await fetch(API + "/api/chat/stream", {
      method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!response.ok) {
      let payload = null;
      try { payload = await response.json(); } catch (_) { /* sin JSON */ }
      const error = new Error(payload && payload.error ? payload.error.message : `Error HTTP ${response.status}`);
      error.code = payload && payload.error ? payload.error.code : "http_error";
      throw error;
    }
    let final = null;
    await readSse(response, ({ name, data }) => {
      if (name === "delta") {
        if (perceived.firstTextMs === undefined) perceived.firstTextMs = performance.now() - started;
        pending.appendDraft(data.text);
      } else if (name === "final") {
        final = data;
      } else if (name === "error") {
        const error = new Error(data.message);
        error.code = data.code;
        throw error;
      }
    });
    if (!final) throw new Error("La respuesta terminó sin resultado verificado.");
    return final;
  }

  function setBusy(on) {
    busy = on;
    $("sendBtn").disabled = on;
    document.querySelectorAll("#suggestions button").forEach((b) => { b.disabled = on; });
    $("chatForm").setAttribute("aria-busy", on ? "true" : "false");
  }

  async function ask(question) {
    const text = question.trim();
    if (!text || busy) return false;         // una pregunta a la vez: evita duplicar solicitudes (y gasto) mientras Kimi responde
    setBusy(true);
    addMessage("user", text);
    const pending = startPending(Boolean(lesson) && lesson.mode === "LIVE");
    const controller = new AbortController();
    const abortTimer = setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);
    const started = performance.now();
    const perceived = {};
    const body = { student_id: STUDENT_ID, course_id: lesson.course_id, lesson_id: lesson.lesson_id, session_id: sessionId, message: text };
    const normalChat = () => api("/api/chat", {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json", "Idempotency-Key": (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)) },
      body: JSON.stringify(body),
    });
    try {
      let reply;
      if (STREAMING) {
        try {
          reply = await streamChat(body, controller, pending, perceived, started);
        } catch (error) {
          if (error && error.code === "streaming_disabled") reply = await normalChat();   // el backend no lo tiene activado: chat normal, sin perder la pregunta
          else throw error;
        }
      } else {
        reply = await normalChat();
      }
      perceived.waitMs = performance.now() - started;
      sessionId = reply.session_id;
      pending.stop();
      renderTutor(reply, perceived);
    } catch (error) {
      pending.stop();
      const timedOut = error && error.name === "AbortError";
      addMessage("tutor", timedOut
        ? `La respuesta tardó más de ${Math.round(CHAT_TIMEOUT_MS / 1000)} s y se canceló. Puedes volver a preguntar.`
        : `No pude responder: ${error.message}`, "oos");
    } finally {
      clearTimeout(abortTimer);
      setBusy(false);
    }
    return true;
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
      if (busy) return;                      // el texto escrito se conserva para cuando termine la respuesta en curso
      const input = $("question");
      const q = input.value;
      if (!q.trim()) return;
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
