class AppError(Exception):
    """Error controlado de la aplicación; se traduce a una respuesta HTTP estructurada."""

    def __init__(self, status_code: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class CourseNotFound(AppError):
    def __init__(self, course_id: str):
        super().__init__(404, "course_not_found", f"No existe el curso '{course_id}'.")


class LessonNotFound(AppError):
    def __init__(self, course_id: str, lesson_id: str):
        super().__init__(
            404, "lesson_not_found", f"El curso '{course_id}' no tiene la clase '{lesson_id}'."
        )


class SessionNotFound(AppError):
    def __init__(self):
        super().__init__(
            404, "session_not_found",
            "La sesión indicada no existe. Omite session_id para iniciar una conversación nueva.",
        )


class SessionMismatch(AppError):
    def __init__(self):
        # Mensaje deliberadamente genérico: no revela a quién pertenece la sesión ni su contenido.
        super().__init__(
            403, "session_mismatch",
            "La sesión no corresponde al estudiante, curso o clase indicados.",
        )
