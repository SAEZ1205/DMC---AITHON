/* Agente Validador — Participación Efectiva (SIMULACIÓN ACADÉMICA, 100 % local).
   Dos preguntas de comprensión sobre la clase de n8n, verificadas con la transcripción real (los tramos de referencia son los minutos
   donde la clase lo explica). No llama al backend ni a ningún modelo, no guarda nada y no registra asistencia ni participación oficial.
   La lógica (QUIZ y gradeQuiz) es pura y se expone en window.RescueValidator para poder probarla. Todo texto se pinta con textContent. */
(function () {
  "use strict";

  /* ---------------------------------------------------------------- contenido */
  const QUIZ = {
    id: "n8n-clase-01-validador",
    questions: [
      {
        id: "q1",
        text: "Una empresa elabora todos los días un reporte repetitivo, siguiendo reglas claras y definidas. ¿Qué conviene hacer con ese proceso?",
        options: [
          { id: "a", text: "Dejarlo manual: los procesos que siguen reglas no se pueden automatizar." },
          { id: "b", text: "Automatizarlo, porque es repetitivo y sus reglas están bien definidas." },
          { id: "c", text: "Automatizar primero los procesos más desordenados, que son los que más tiempo toman." },
          { id: "d", text: "Esperar a que cambien las reglas cada día antes de decidir." },
        ],
        correct: "b",
        reference: { startSeconds: 558, label: "09:18–10:10" },       // «¿Qué procesos conviene automatizar?»
        explanation: "En la clase se explica que conviene automatizar los procesos repetitivos y con reglas ordenadas y bien definidas; si las reglas no están claras, el proceso no se puede optimizar.",
        hint: "Piensa en qué características tiene un buen candidato a automatizar: ¿se repite? ¿sus reglas están claras?",
      },
      {
        id: "q2",
        text: "Una aplicación antigua no dispone de API y un usuario debe hacer clic en botones (siguiente, subir, cargar el archivo) para ejecutar sus tareas. ¿Qué alternativa es más adecuada?",
        options: [
          { id: "a", text: "Un workflow que se conecte directamente a la aplicación mediante su API." },
          { id: "b", text: "Una herramienta low-code, escribiendo código para llamar a una API que no existe." },
          { id: "c", text: "RPA, que interactúa con la interfaz como lo haría una persona (clics y botones)." },
          { id: "d", text: "Ninguna: una aplicación sin API no se puede automatizar." },
        ],
        correct: "c",
        reference: { startSeconds: 1544, label: "25:44–26:15" },       // «¿Cuándo se usa un workflow?… cuando no tenemos un API… es un RPA»
        explanation: "En la clase se distingue: si hay una API conviene un workflow; cuando no hay API y hay que hacer clic en botones de una interfaz, se usa un RPA.",
        hint: "Pregúntate si la aplicación ofrece una API y qué haría una persona frente a su pantalla.",
      },
    ],
  };

  /* ------------------------------------------------------------ lógica pura */
  /* answers: { q1: "b", q2: "c" } -> { total, answered, complete, correct, validated, results: [{id, answered, correct}] } */
  function gradeQuiz(answers, quiz) {
    const q = quiz || QUIZ;
    const given = answers || {};
    const results = q.questions.map((question) => {
      const chosen = Object.prototype.hasOwnProperty.call(given, question.id) ? given[question.id] : null;
      const valid = question.options.some((o) => o.id === chosen);
      return { id: question.id, answered: valid, correct: valid && chosen === question.correct };
    });
    const answered = results.filter((r) => r.answered).length;
    const complete = answered === q.questions.length;
    const correct = complete ? results.filter((r) => r.correct).length : 0;   // sin las dos respuestas no se califica
    return { total: q.questions.length, answered, complete, correct, validated: complete && correct === q.questions.length, results };
  }

  const api = { QUIZ, gradeQuiz };
  if (typeof window === "undefined") {          // pruebas en Node
    if (typeof module !== "undefined") module.exports = api;
    return;
  }
  window.RescueValidator = api;

  /* ------------------------------------------------------------------ interfaz */
  const $ = (id) => document.getElementById(id);
  const form = $("quizQuestions");
  if (!form) return;
  let attempt = 1;
  let locked = false;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function render() {
    form.replaceChildren();
    QUIZ.questions.forEach((question, qi) => {
      const box = el("fieldset", "demo-q");
      box.id = `quiz-${question.id}`;
      box.append(el("legend", "", `Pregunta ${qi + 1} de ${QUIZ.questions.length}`), el("p", "demo-q-text", question.text));
      question.options.forEach((option) => {
        const label = el("label", "demo-opt");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = question.id;
        input.value = option.id;
        input.addEventListener("change", onChange);
        label.append(input, el("span", "", option.text));
        box.append(label);
      });
      box.append(el("div", "demo-q-feedback"));
      form.append(box);
    });
  }

  function answers() {
    const out = {};
    QUIZ.questions.forEach((question) => {
      const checked = form.querySelector(`input[name="${question.id}"]:checked`);
      if (checked) out[question.id] = checked.value;
    });
    return out;
  }

  function setResult(kind, lines) {
    const box = $("quizResult");
    box.className = "demo-quiz-result" + (kind ? " " + kind : "");
    box.replaceChildren(...lines.map((text, i) => el("p", i === 0 ? "demo-quiz-score" : "", text)));
  }

  function onChange() {
    if (locked) return;
    const n = Object.keys(answers()).length;
    $("quizHint").textContent = n === QUIZ.questions.length ? "Listo: pulsa «Validar respuestas»." : `Respondidas: ${n} de ${QUIZ.questions.length}.`;
  }

  function jumpTo(seconds) {
    window.RescueDemo && window.RescueDemo.showTab("class");     // volver a la clase sin recargar nada
    window.RescueDemo && window.RescueDemo.seekTo(seconds);
  }

  function validate() {
    if (locked) return;
    const graded = gradeQuiz(answers());
    if (!graded.complete) {
      setResult("warn", ["Responde las dos preguntas para poder validar.", `Te faltan ${graded.total - graded.answered}.`]);
      const first = graded.results.find((r) => !r.answered);
      const box = first && $(`quiz-${first.id}`);
      if (box) box.querySelector("input").focus();
      return;
    }
    locked = true;
    form.querySelectorAll("input").forEach((i) => { i.disabled = true; });
    $("quizValidate").disabled = true;
    $("quizRetry").hidden = false;
    $("quizHint").textContent = "Respuestas validadas.";
    QUIZ.questions.forEach((question) => {
      const result = graded.results.find((r) => r.id === question.id);
      const box = $(`quiz-${question.id}`);
      box.classList.add(result.correct ? "ok" : "bad");
      const feedback = box.querySelector(".demo-q-feedback");
      feedback.replaceChildren(el("p", "demo-q-verdict", result.correct ? "✔ Correcto" : "✘ Aún no"),
        el("p", "", result.correct ? question.explanation : question.hint));
      const btn = el("button", "demo-cite", `▶ Ver en el video · ${question.reference.label}`);
      btn.type = "button";
      btn.addEventListener("click", () => jumpTo(question.reference.startSeconds));
      feedback.append(btn);
    });
    const score = `${graded.correct} de ${graded.total} respuestas correctas`;
    if (graded.validated) {
      setResult("ok", [score, "✔ Comprensión validada (simulación académica).",
        "Es una prueba de concepto: no registra asistencia ni participación oficial."]);
    } else {
      setResult("warn", [score, "Revisa los minutos indicados en cada pregunta y pulsa «Volver a intentar».",
        "Simulación académica: no se registra nada."]);
    }
  }

  function retry() {
    attempt += 1;
    locked = false;
    render();
    $("quizValidate").disabled = false;
    $("quizRetry").hidden = true;
    $("quizAttempt").textContent = `Intento ${attempt}`;
    $("quizHint").textContent = `Respondidas: 0 de ${QUIZ.questions.length}.`;
    setResult("", []);
    const first = form.querySelector("input");
    if (first) first.focus();
  }

  /* ------------------------------------------------------------------- pestañas */
  const tabs = { class: { tab: $("tabClass"), panel: $("panelClass") }, validator: { tab: $("tabValidator"), panel: $("panelValidator") } };

  function showTab(name) {
    if (!tabs[name]) return;
    const player = $("player");
    if (name !== "class" && player && !player.paused) player.pause();       // el audio no sigue sonando detrás; la posición se conserva
    Object.keys(tabs).forEach((key) => {
      const on = key === name;
      tabs[key].panel.hidden = !on;                                           // se OCULTA, no se destruye: chat, video y resumen conservan su estado
      tabs[key].tab.setAttribute("aria-selected", String(on));
      tabs[key].tab.classList.toggle("active", on);
      tabs[key].tab.tabIndex = on ? 0 : -1;
    });
    try { history.replaceState(null, "", name === "validator" ? "#validar" : location.pathname + location.search); } catch (_) { /* sin historial */ }
  }

  Object.keys(tabs).forEach((key) => {
    tabs[key].tab.addEventListener("click", () => showTab(key));
    tabs[key].tab.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        const next = key === "class" ? "validator" : "class";
        showTab(next);
        tabs[next].tab.focus();
      }
    });
  });

  window.RescueDemo = Object.assign(window.RescueDemo || {}, { showTab });
  $("quizValidate").addEventListener("click", validate);
  $("quizRetry").addEventListener("click", retry);
  render();
  $("quizAttempt").textContent = `Intento ${attempt}`;
  if (location.hash === "#validar") showTab("validator");
})();
