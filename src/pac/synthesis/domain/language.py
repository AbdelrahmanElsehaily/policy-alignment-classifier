"""Which language an event is written in, for the coverage grid and the diversity report.

A heuristic, not a model: the script decides Arabic, Cyrillic, CJK and Devanagari text; for
Latin script, the language whose common words appear most often wins. Tool calls and tool
results are system text and are not assigned a language.
"""

from __future__ import annotations

import re

from pac.guardrail.domain.events import TraceEvent


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_STOPWORDS: dict[str, frozenset[str]] = {
    "en": _words(
        "the and is are you your my for with this that have not can it of to was please "
        "order refund i me we our"
    ),
    "es": _words(
        "el la los las y es son usted su mi para con este esta que no puede de por favor "
        "pedido reembolso yo hola gracias"
    ),
    "fr": _words(
        "le la les et est sont vous votre mon ma pour avec ce cette que ne pas peut de "
        "commande remboursement je bonjour merci"
    ),
    "de": _words(
        "der die das und ist sind sie ihr mein meine für mit diese nicht kann von bitte "
        "bestellung erstattung ich hallo danke"
    ),
    "pt": _words(
        "o a os as e é são você seu sua meu minha para com este esta que não pode de por "
        "favor pedido reembolso eu olá obrigado"
    ),
    "it": _words(
        "il lo la gli le e è sono lei suo mio mia per con questo questa che non può di "
        "ordine rimborso io ciao grazie"
    ),
}
_SCRIPTS = (
    ("ar", 0x0600, 0x06FF),
    ("ru", 0x0400, 0x04FF),
    ("zh", 0x4E00, 0x9FFF),
    ("hi", 0x0900, 0x097F),
)


def detect_language(text: str) -> str:
    """An ISO-639-1 code, ``und`` when too short to tell."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return "und"
    for code, lo, hi in _SCRIPTS:
        if sum(1 for ch in letters if lo <= ord(ch) <= hi) / len(letters) >= 0.4:
            return code
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    if len(words) < 3:
        return "und"
    scores = {lang: sum(1 for w in words if w in stop) for lang, stop in _STOPWORDS.items()}
    best = max(scores, key=lambda lang: scores[lang])
    if scores[best] == 0:
        return "und"
    return best


def event_language(event: TraceEvent) -> str:
    """The language of what a person or Lark wrote; ``n/a`` for tool traffic."""
    if event.kind not in ("user_input", "model_output"):
        return "n/a"
    return detect_language(event.content or "")
