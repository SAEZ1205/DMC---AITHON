Clasifica la pregunta de un alumno al chat de una grabación de clase.

- `contenido`: dudas sobre el tema de la clase (conceptos, herramientas, ejercicios).
- `logistica`: fechas, entregas, notas, certificados, pagos, horarios, material, grabaciones.
- `otro`: saludos, comentarios o cualquier otra cosa.

Pregunta: «{{MESSAGE}}»

Responde solo con JSON: {"kind": "contenido" | "logistica" | "otro"}
