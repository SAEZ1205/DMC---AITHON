"""
Banco de preguntas de quiz (DEMO, revisable y determinista).

Cada pregunta debe estar FUNDAMENTADA en un segmento de la transcripción: el archivo indica el
`segment_id` y una `evidence_quote` que tiene que aparecer textualmente en ese segmento. Si no aparece,
la pregunta se rechaza al cargar (y queda registrada en `load_errors`), de modo que no puede entrar al quiz
una pregunta cuya respuesta no esté respaldada por la clase.
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.knowledge.retriever import KnowledgeRetriever
from app.knowledge.text_utils import normalize_text
from app.models.transcript import TranscriptSegment

logger = logging.getLogger(__name__)

QUIZZES_DIR = Path(__file__).resolve().parent / "quizzes"
OPTIONS_PER_QUESTION = 4
MIN_QUESTIONS_FOR_QUIZ = 3


@dataclass(frozen=True)
class BankQuestion:
    question_id: str
    concept: str
    prompt: str
    options: Tuple[str, ...]
    correct_index: int
    explanation: str
    segment: TranscriptSegment
    lesson_title: str


def _squash(text: str) -> str:
    return " ".join(normalize_text(text).split())


class QuizBank:
    def __init__(self, retriever: KnowledgeRetriever, quizzes_dir: Optional[Path] = None):
        self._retriever = retriever
        self._dir = Path(quizzes_dir) if quizzes_dir is not None else QUIZZES_DIR
        self._questions: Dict[Tuple[str, str], List[BankQuestion]] = {}
        self._disclaimers: Dict[Tuple[str, str], str] = {}
        self.load_errors: List[str] = []
        self._load()

    # ------------------------------------------------------------------
    def _error(self, message: str) -> None:
        self.load_errors.append(message)
        logger.warning("[QuizBank] %s", message)

    def _load(self) -> None:
        if not self._dir.exists():
            return
        for course_dir in sorted(p for p in self._dir.iterdir() if p.is_dir()):
            for path in sorted(course_dir.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8-sig"))
                    self._load_file(course_dir.name, path.stem, data)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    self._error(f"{course_dir.name}/{path.name}: archivo inválido ({exc})")

    def _load_file(self, course_id: str, lesson_id: str, data: dict) -> None:
        lesson = self._retriever.get_lesson(course_id, lesson_id)
        if lesson is None:
            self._error(f"{course_id}/{lesson_id}: no existe la clase con transcripción")
            return
        if data.get("course_id") != course_id or data.get("lesson_id") != lesson_id:
            self._error(f"{course_id}/{lesson_id}: course_id/lesson_id del archivo no coinciden con su ruta")
            return
        valid: List[BankQuestion] = []
        seen_ids = set()
        for raw in data.get("questions", []):
            problem = self._problem(raw, lesson, seen_ids)
            if problem:
                self._error(f"{course_id}/{lesson_id}/{raw.get('question_id', '?')}: {problem}")
                continue
            seen_ids.add(raw["question_id"])
            valid.append(BankQuestion(
                question_id=raw["question_id"], concept=raw["concept"], prompt=raw["prompt"].strip(),
                options=tuple(o.strip() for o in raw["options"]), correct_index=raw["correct_index"],
                explanation=raw["explanation"].strip(), segment=lesson.segments[raw["segment_id"]],
                lesson_title=lesson.title,
            ))
        self._questions[(course_id, lesson_id)] = valid
        self._disclaimers[(course_id, lesson_id)] = data.get("disclaimer", "")

    @staticmethod
    def _problem(raw: dict, lesson, seen_ids: set) -> Optional[str]:
        for key in ("question_id", "concept", "prompt", "options", "correct_index", "explanation", "segment_id", "evidence_quote"):
            if key not in raw:
                return f"falta el campo '{key}'"
        if raw["question_id"] in seen_ids:
            return "question_id duplicado"
        options = raw["options"]
        if not isinstance(options, list) or len(options) != OPTIONS_PER_QUESTION:
            return f"debe tener exactamente {OPTIONS_PER_QUESTION} alternativas"
        if not all(isinstance(o, str) and o.strip() for o in options):
            return "alternativa vacía o no textual"
        if len({_squash(o) for o in options}) != OPTIONS_PER_QUESTION:
            return "alternativas repetidas"
        if not isinstance(raw["correct_index"], int) or not 0 <= raw["correct_index"] < OPTIONS_PER_QUESTION:
            return "correct_index fuera de rango"
        if not isinstance(raw["prompt"], str) or len(raw["prompt"].strip()) < 10 or not raw["prompt"].strip().endswith("?"):
            return "el enunciado debe ser una pregunta"
        if not isinstance(raw["explanation"], str) or not raw["explanation"].strip():
            return "falta la explicación"
        sid = raw["segment_id"]
        if not isinstance(sid, int) or not 0 <= sid < len(lesson.segments):
            return "segment_id inexistente en la clase"
        if _squash(raw["evidence_quote"]) not in _squash(lesson.segments[sid].text):
            return "la evidence_quote no aparece textualmente en el segmento referenciado"
        return None

    # ------------------------------------------------------------------
    def questions(self, course_id: str, lesson_id: str) -> List[BankQuestion]:
        return list(self._questions.get((course_id, lesson_id), []))

    def has_quiz(self, course_id: str, lesson_id: str) -> bool:
        return len(self._questions.get((course_id, lesson_id), [])) >= MIN_QUESTIONS_FOR_QUIZ

    def disclaimer(self, course_id: str, lesson_id: str) -> str:
        return self._disclaimers.get((course_id, lesson_id), "")
