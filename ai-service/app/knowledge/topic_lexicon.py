"""
Léxico temático para titular capítulos de clases largas SIN fichas de tema (p. ej. la clase real de n8n).

Cómo funciona (determinista, sin modelo de lenguaje)
  * Cada tema tiene un título académico y una lista de términos (como los escribe el reconocimiento automático de voz, con sus
    variantes: «NHN», «HTP»…). Un tema solo puede titular un bloque si sus términos APARECEN en la transcripción de ese bloque:
    se exigen un término «ancla» y un mínimo de términos distintos, y se descuentan los temas presentes en casi toda la clase.
  * Si ningún tema alcanza la evidencia mínima, el bloque conserva el título calculado a partir de sus propias palabras.
  * El título es una ETIQUETA TEMÁTICA para navegar; no es una cita del docente ni afirma que el tema se «explicó».

El léxico es DATO (editable, versionado con `LEXICON_VERSION`): cambiarlo regenera el resumen heurístico una sola vez.
"""
import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from app.knowledge.text_utils import tokenize

LEXICON_VERSION = "automation-es/1"
MIN_SCORE = 3.0              # evidencia mínima de un tema en un bloque (suma de pesos con rendimiento decreciente)
CAP_PER_TERM = 6             # una palabra repetida muchas veces no basta por sí sola
SECOND_TOPIC_RATIO = 0.8     # un segundo tema acompaña al primero solo si casi lo iguala
MAX_TOPIC_BLOCK_RATIO = 0.6  # un tema presente en >60 % de los bloques no distingue ninguno


@dataclass(frozen=True)
class LexiconTopic:
    key: str
    title: str
    terms: Tuple[Tuple[str, float], ...]       # (palabra o frase normalizada, peso)
    anchors: Tuple[str, ...]                    # al menos UNO debe aparecer
    min_terms: int = 3                          # términos distintos mínimos
    where: Optional[str] = None                 # "start" | "end": solo se evalúa en el primer/último bloque
    short: str = ""                             # nombre breve para unir dos temas en un mismo título


def _t(words: str, weight: float = 1.0) -> List[Tuple[str, float]]:
    return [(w, weight) for w in words.split()]


def _short_names() -> Dict[str, str]:
    return {
        "intro": "presentación de la sesión", "automation": "automatización de procesos", "n8n": "n8n", "nocode": "no-code y low-code",
        "rpa": "RPA", "workflows": "workflows y triggers", "apis": "APIs y webhooks", "docker": "Docker", "setup": "preparación del entorno",
        "local": "n8n en local", "dates": "fechas y condiciones", "messaging": "WhatsApp y Telegram", "google": "integración con Google",
        "slack": "Slack y correo", "errors": "errores y reintentos", "data": "datos y variables", "course": "materiales del curso",
        "closing": "cierre",
    }


LEXICON: Tuple[LexiconTopic, ...] = (
    LexiconTopic("intro", "Presentación de la sesión y objetivos",
                 tuple(_t("bienvenidos bienvenidas hoy agenda objetivo objetivos temas vamos") + [("vamos a ver", 1.5)]),
                 anchors=("bienvenidos", "bienvenidas", "objetivo", "objetivos", "agenda"), min_terms=2, where="start"),
    LexiconTopic("automation", "Introducción a la automatización de procesos",
                 tuple(_t("automatizar automatizacion automatizado automatizados procesos proceso manual manuales repetitivo repetitivos "
                          "reglas empresa tareas") + [("basado en reglas", 2.0)]),
                 anchors=("automatizar", "automatizacion", "automatizado", "automatizados"), min_terms=3),
    LexiconTopic("n8n", "n8n: plataforma de automatización de flujos",
                 tuple(_t("n8n nhn hnn hn", 1.5) + _t("plataforma nodos nodo flujos licencia") + [("open source", 2.0), ("self hosted", 2.0)]),
                 anchors=("n8n", "nhn", "hnn"), min_terms=3),
    LexiconTopic("nocode", "No-code y low-code",
                 tuple([("no code", 2.0), ("low code", 2.0), ("nocode", 2.0), ("lowcode", 2.0)] + _t("codigo tecnico conocimiento nube")),
                 anchors=("no code", "low code", "nocode", "lowcode"), min_terms=2),
    LexiconTopic("rpa", "RPA y automatización de escritorio",
                 tuple(_t("rpa", 2.0) + [("power automate", 2.0), ("uipath", 2.0)] + _t("robot robots bot bots escritorio desktop automate pantalla")),
                 anchors=("rpa", "power automate", "uipath"), min_terms=2),
    LexiconTopic("workflows", "Workflows, nodos y triggers",
                 tuple(_t("workflow workflows wf trigger triggers nodo nodos accion acciones flujo flujos conectar conexion canvas ejecutar")
                       + [("manual trigger", 2.0)]),
                 anchors=("workflow", "workflows", "wf", "trigger", "triggers", "nodo", "nodos"), min_terms=3),
    LexiconTopic("apis", "APIs, HTTP y webhooks",
                 tuple(_t("api apis http htp hcp request webhook webhooks json jason endpoint url rest header headers metodo get post respuesta")),
                 anchors=("api", "apis", "http", "htp", "hcp", "webhook", "webhooks", "request"), min_terms=3),
    LexiconTopic("docker", "Docker y contenedores",
                 tuple(_t("docker dockers", 1.5) + _t("contenedor contenedores imagen imagenes containerizar compose virtualizacion hub")),
                 anchors=("docker", "dockers", "contenedor", "contenedores"), min_terms=2),
    LexiconTopic("setup", "Preparación del entorno: Docker Desktop, Git, WSL y terminal",
                 tuple(_t("instalar instalacion instalado instalar descargar descarga git wsl terminal powershell shell administrador reiniciar "
                          "reiniciado reinicia bios windows mac carpeta clonar comando linux") + [("power shell", 2.0), ("docker desktop", 2.0)]),
                 anchors=("git", "wsl", "powershell", "terminal", "docker desktop", "power shell", "instalar", "instalacion"), min_terms=5),
    LexiconTopic("local", "Puesta en marcha de n8n en local (puerto, URL y contenedor)",
                 tuple(_t("puerto localhost levantar running url inicio") + [("docker compose", 2.0), ("5678", 2.0)]),
                 anchors=("puerto", "localhost", "docker compose", "running", "5678"), min_terms=3),
    LexiconTopic("dates", "Fechas, horas, formatos y condiciones",
                 tuple(_t("hora horas fecha fechas formato zona horaria iso condicion condiciones true false comparar expresion expresiones")
                       + [("current time", 2.0), ("america lima", 1.5)]),
                 anchors=("hora", "horas", "fecha", "fechas", "formato", "condicion", "condiciones", "current time"), min_terms=4),
    LexiconTopic("messaging", "Mensajería: WhatsApp y Telegram",
                 tuple(_t("whatsapp telegram", 2.0) + _t("mensaje mensajes chat bot meta")),
                 anchors=("whatsapp", "telegram"), min_terms=2),
    LexiconTopic("google", "Integración con Google: Cloud, Sheets y credenciales",
                 tuple(_t("google gmail drive sheets sheet cloud credencial credenciales proyecto habilitar oauth token autorizar") +
                       [("client id", 2.0), ("google cloud", 2.0)]),
                 anchors=("google", "gmail", "drive", "sheets", "sheet", "google cloud"), min_terms=4),
    LexiconTopic("slack", "Notificaciones con Slack y correo",
                 tuple(_t("slack", 2.0) + _t("canal canales notificacion notificaciones correo email smtp mensaje")),
                 anchors=("slack", "smtp"), min_terms=2),
    LexiconTopic("errors", "Manejo de errores y reintentos",
                 tuple(_t("error errores reintento reintentos retry maximo fallo fallido continue ocurrido") + [("error workflow", 2.0)]),
                 anchors=("error", "errores", "reintento", "reintentos", "retry"), min_terms=3),
    LexiconTopic("data", "Datos, variables y tablas",
                 tuple(_t("variable variables entorno filas fila columnas tabla excel hoja campo campos dato datos set")),
                 anchors=("variable", "variables", "filas", "excel", "tabla", "campos"), min_terms=4),
    LexiconTopic("course", "Plataforma y materiales del curso",
                 tuple(_t("materiales material plataforma sesion curso grabacion zoom tarea tareas")),
                 anchors=("materiales", "plataforma", "grabacion", "zoom"), min_terms=3),
    LexiconTopic("closing", "Cierre y próximos pasos",
                 tuple(_t("terminar cierre proxima proximo tarea practicar gracias hasta") + [("hasta aqui", 2.0), ("la proxima", 2.0)]),
                 anchors=("cierre", "terminar", "tarea", "proxima", "hasta aqui", "la proxima"), min_terms=3, where="end"),
)


@dataclass(frozen=True)
class TopicEvidence:
    topic: LexiconTopic
    score: float
    terms_found: Tuple[Tuple[str, int], ...]      # (término, apariciones) de mayor a menor


def _count(norm: str, term: str) -> int:
    return norm.count(f" {term} ")


def score_window(text: str, position: str = "middle") -> Dict[str, TopicEvidence]:
    """Evidencia de cada tema en un bloque de transcripción. `position`: start | middle | end."""
    norm = " " + " ".join(tokenize(text)) + " "
    found: Dict[str, TopicEvidence] = {}
    for topic in LEXICON:
        if topic.where and topic.where != position:
            continue
        counts = [(term, weight, _count(norm, term)) for term, weight in topic.terms]
        present = [(term, weight, c) for term, weight, c in counts if c > 0]
        if len(present) < topic.min_terms or not any(_count(norm, a) > 0 for a in topic.anchors):
            continue
        score = sum(weight * math.log(1 + min(c, CAP_PER_TERM)) for _, weight, c in present)
        if score >= MIN_SCORE:
            found[topic.key] = TopicEvidence(topic, round(score, 3), tuple(sorted(((t, c) for t, _, c in present), key=lambda x: -x[1])[:6]))
    return found


def title_windows(texts: Sequence[str]) -> List[Optional[Tuple[str, Tuple[str, ...]]]]:
    """
    Un título temático (y las claves de tema usadas) por bloque, o None si ningún tema tiene evidencia suficiente.
    Los temas presentes en casi todos los bloques se descuentan (no distinguen ninguno).
    """
    n = len(texts)
    per_window = [score_window(t, "start" if i == 0 else ("end" if i == n - 1 else "middle")) for i, t in enumerate(texts)]
    presence: Dict[str, int] = {}
    for found in per_window:
        for key in found:
            presence[key] = presence.get(key, 0) + 1
    titles: List[Optional[Tuple[str, Tuple[str, ...]]]] = []
    for found in per_window:
        ranked = []
        for key, ev in found.items():
            share = presence[key] / max(n, 1)
            if n > 4 and share > MAX_TOPIC_BLOCK_RATIO:
                continue
            ranked.append((ev.score * math.log(1 + n / presence[key]), key, ev))
        ranked.sort(key=lambda x: -x[0])
        if not ranked:
            titles.append(None)
            continue
        chosen = [ranked[0]]
        if len(ranked) > 1 and ranked[1][0] >= ranked[0][0] * SECOND_TOPIC_RATIO:
            chosen.append(ranked[1])
        titles.append((" · ".join(ev.topic.title if i == 0 else (ev.topic.short or ev.topic.title) for i, (_, _, ev) in enumerate(chosen)),
                       tuple(k for _, k, _ in chosen)))
    return titles


def topic_terms(keys: Sequence[str]) -> List[str]:
    """Palabras sueltas (normalizadas) de los temas dados: sirven para elegir la frase más representativa de un capítulo."""
    words: List[str] = []
    for topic in LEXICON:
        if topic.key in keys:
            words += [t for t, _ in topic.terms if " " not in t]
    return list(dict.fromkeys(words))


LEXICON = tuple(
    LexiconTopic(t.key, t.title, tuple(dict.fromkeys(t.terms)), t.anchors, t.min_terms, t.where, _short_names().get(t.key, t.title)) for t in LEXICON
)
