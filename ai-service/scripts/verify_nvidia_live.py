"""
Primera prueba REAL y MANUAL de NVIDIA Build (Kimi K3): UNA sola solicitud corta con texto FICTICIO e inocuo.

NO forma parte de pytest y NUNCA se ejecuta sola. Solo hace la solicitud si pasas el flag explícito:

    cd ai-service
    .\\.venv\\Scripts\\python.exe scripts\\verify_nvidia_live.py --i-authorize-one-nvidia-request

Qué hace
  * Lee NVIDIA_API_KEY del entorno privado (ai-service\\.env, ignorado por Git). No la imprime nunca.
  * Envía exactamente UNA solicitud con la pregunta ficticia «Explica brevemente qué es una variable en programación.»
    (sin transcripción, sin datos de estudiantes, sin video).
  * Limita los tokens de salida (--max-tokens, por defecto 600) y usa reasoning_effort=low.
  * Informa estado HTTP, modelo, tokens y si la respuesta fue generada realmente por NVIDIA.
  * Si falla, se detiene: no hay reintentos automáticos.

Aviso: NVIDIA puede registrar entradas y salidas del servicio de prueba. Aquí solo viaja texto ficticio.
"""
import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataclasses import replace  # noqa: E402

from app.config import LLMConfig, settings  # noqa: E402
from app.llm.nvidia_provider import PROBE_PROMPT, NvidiaKimiProvider  # noqa: E402

MAX_TOKENS_LIMIT = 2000


def main(argv=None, config: Optional[LLMConfig] = None, client=None) -> int:
    parser = argparse.ArgumentParser(description="Una única solicitud real y corta a NVIDIA Build (texto ficticio)")
    parser.add_argument("--i-authorize-one-nvidia-request", action="store_true",
                        help="autorizo UNA solicitud real a NVIDIA con texto ficticio")
    parser.add_argument("--max-tokens", type=int, default=600, help=f"tokens de salida (máx. {MAX_TOKENS_LIMIT}); el razonamiento cuenta")
    parser.add_argument("--effort", choices=("low", "high", "max"), default="low")
    args = parser.parse_args(argv)

    cfg = config or settings.nvidia_config()
    if not args.i_authorize_one_nvidia_request:
        print("Abortado: falta --i-authorize-one-nvidia-request. No se hizo ninguna solicitud.")
        return 2
    if not cfg.api_key:
        print("Abortado: NVIDIA_API_KEY no está definida. Añádela en ai-service\\.env (ver DEMO_JUEVES.md). No se hizo ninguna solicitud.")
        return 2
    if not 16 <= args.max_tokens <= MAX_TOKENS_LIMIT:
        print(f"Abortado: --max-tokens debe estar entre 16 y {MAX_TOKENS_LIMIT}. No se hizo ninguna solicitud.")
        return 2
    try:
        provider = NvidiaKimiProvider(replace(cfg, max_live_calls=1), client=client)
    except ValueError as exc:
        print(f"Abortado: configuración inválida ({exc}). No se hizo ninguna solicitud.")
        return 2

    host = cfg.base_url.split("//", 1)[-1].split("/", 1)[0]
    print("Prueba NVIDIA Build — UNA solicitud, texto ficticio")
    print(f"  Modelo configurado : {cfg.model}")
    print(f"  Servidor           : {host} (HTTPS)")
    print(f"  Pregunta ficticia  : {PROBE_PROMPT}")
    print(f"  Tokens de salida   : {args.max_tokens} · reasoning_effort={args.effort} · sin streaming")
    print("  Enviando... (sin reintentos)")
    try:
        result = provider.probe(PROBE_PROMPT, max_tokens=args.max_tokens, effort=args.effort)
    finally:
        provider.close()

    print(f"\nCódigo de estado HTTP         : {result.status_code if result.status_code is not None else 'sin respuesta'}")
    if not result.ok:
        print(f"RESULTADO: FALLÓ [{result.error_code}] {result.error_message}")
        print("Se detuvo aquí: no se hacen reintentos automáticos. Revisa la causa antes de volver a intentarlo.")
        return 1
    print(f"Modelo informado por NVIDIA   : {result.model or '(no informado)'}")
    print(f"Generada realmente por NVIDIA : SÍ (respuesta HTTP 200 de {host} con 'choices')" if result.generated_by_nvidia
          else "Generada realmente por NVIDIA : no se pudo confirmar")
    print(f"finish_reason                 : {result.finish_reason}")
    print(f"Tokens                        : {result.usage or '(sin datos de uso)'}")
    print(f"Razonamiento recibido         : {'sí (no se muestra ni se guarda)' if result.reasoning_present else 'no'}")
    print(f"Tiempo                        : {result.elapsed_seconds} s")
    print(f"\nRespuesta (texto ficticio):\n{result.content}")
    print("\nRESULTADO: OK. Esto NO activa el chat con NVIDIA: para eso hay que configurar LLM_PROVIDER=nvidia y LLM_LIVE_AUTHORIZED=true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
