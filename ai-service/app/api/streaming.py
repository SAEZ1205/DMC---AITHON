"""
Chat con streaming (SSE) — OPCIONAL y apagado por defecto (CHAT_STREAMING_ENABLED=false). El endpoint `/api/chat` no cambia.

Eventos (cada `data:` es JSON en una sola línea; el navegador los pinta con textContent):
  status     {"stage": "started"}                       la solicitud llegó
  delta      {"text": "..."}                             texto PROVISIONAL de la respuesta (sin etiquetas de cita). NO es una referencia
  final      ChatResponse completo                       la respuesta OFICIAL: citas y minutos verificados por el backend (reemplaza al provisional)
  error      {"code", "message"}                          fallo controlado (la interfaz muestra el mensaje)
  cancelled  {}                                           el servidor detuvo la llamada porque el cliente se fue

Garantías: el razonamiento interno del modelo nunca se reenvía; las citas navegables salen SOLO del evento `final`; si el cliente cierra la conexión
se corta la llamada al proveedor y no se guarda el turno; el mismo servicio (guardián, recuperador, privacidad, verificador, fallback DEMO, caché)
atiende la solicitud: solo cambia cómo se entrega el texto.
"""
import json
import logging
import queue
import threading
from typing import Any, Iterator

from app.llm.errors import StreamCancelled
from app.services.errors import AppError

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 15.0


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def sse_events(service, payload, heartbeat: float = HEARTBEAT_SECONDS) -> Iterator[str]:
    events: "queue.Queue" = queue.Queue()
    cancel = threading.Event()

    def worker() -> None:
        try:
            response = service.chat(payload, on_delta=lambda text: events.put(("delta", {"text": text})), should_cancel=cancel.is_set)
            events.put(("final", response.model_dump(mode="json")))
        except StreamCancelled:
            events.put(("cancelled", {}))
        except AppError as exc:
            events.put(("error", {"code": exc.code, "message": exc.message}))
        except Exception:                                     # nunca se filtran detalles internos
            logger.exception("Fallo inesperado en el chat con streaming")
            events.put(("error", {"code": "internal_error", "message": "Ocurrió un error interno al procesar tu pregunta."}))
        finally:
            events.put(None)

    threading.Thread(target=worker, name="chat-stream", daemon=True).start()
    try:
        yield sse("status", {"stage": "started"})
        while True:
            try:
                item = events.get(timeout=heartbeat)
            except queue.Empty:
                yield ": keep-alive\n\n"                      # evita que proxies cierren una conexión silenciosa mientras Kimi razona
                continue
            if item is None:
                break
            yield sse(*item)
    finally:
        cancel.set()                                          # cliente desconectado (o fin normal): el proveedor deja de leer
