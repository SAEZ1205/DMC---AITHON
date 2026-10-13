Eres docente de DMC Institute. Crea un banco de {{N}} preguntas de opción múltiple en español a partir de los tramos clave de esta clase.

Reglas:
- Cada pregunta tiene exactamente 4 alternativas y una sola correcta (`answer_idx` de 0 a 3).
- Las alternativas incorrectas deben ser plausibles.
- Evalúa comprensión de lo que se explicó, no detalles triviales.
- `explanation`: una frase que explica la respuesta correcta y cita el minuto entre corchetes, por ejemplo [15:00].
- Usa solo información de los tramos clave.

Tramos clave:
{{KEY_CONTENT}}

Responde solo con JSON: {"items": [{"question": "...", "options": ["...", "...", "...", "..."], "answer_idx": 0, "explanation": "..."}]}
