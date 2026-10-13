from app.knowledge.text_utils import (
    contains_phrase, content_stems, normalize_text, stem, strip_accents, tokenize,
)


def test_strip_accents_and_case():
    assert strip_accents("Proyección ÁÉÍÓÚ ñandú") == "Proyeccion AEIOU nandu"
    assert normalize_text("AGREGACIÓN") == "agregacion"


def test_accented_and_unaccented_forms_share_the_same_stems():
    assert content_stems("proyección") == content_stems("PROYECCION") == content_stems("proyeccion")
    assert content_stems("condición") == content_stems("condiciones")


def test_stopwords_are_removed():
    assert content_stems("¿Qué es una tabla de la base?") == ["tabla", "base"]


def test_meta_words_about_the_recording_are_ignored():
    assert content_stems("¿En qué minuto de la clase explican ORDER BY?") == ["order", "by"]


def test_plural_and_derivations_collapse():
    assert stem("columnas") == stem("columna")
    assert stem("ordenar") == stem("orden") == stem("ordenamiento")
    assert stem("joins") == "join"


def test_tokenize_splits_identifiers_and_never_matches_substrings():
    assert tokenize("id_estudiante") == ["id", "estudiante"]
    # "on" es un token completo solo cuando aparece solo, no dentro de "condiciones"
    assert "on" not in tokenize("condiciones")
    assert "on" in tokenize("JOIN x ON y")


def test_contains_phrase_requires_contiguity():
    seq = ["explic", "inner", "join", "tabla"]
    assert contains_phrase(seq, ["inner", "join"])
    assert not contains_phrase(seq, ["inner", "tabla"])
    assert not contains_phrase(seq, [])
