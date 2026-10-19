"""Package loggers. Log records may carry ids, counts, hashes and timings only.

`RedactingFilter` is a second line of defence: every original username and
`extra_names` entry seen during preprocessing is registered here, and any
occurrence in a log message (ignoring case and accents) becomes ``<redacted>``.
Filters on a parent logger do not run for child loggers, so every module must
obtain its logger through `get_logger`, which attaches the filter.
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata

PACKAGE = "fgpd_analyzer"


def _fold(text: str) -> str:
    out = []
    for c in unicodedata.normalize("NFC", text):
        base = unicodedata.normalize("NFD", c)[0]
        low = base.lower()
        out.append(low if len(low) == 1 else base)
    return "".join(out)


class RedactingFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._names: set[str] = set()
        self._lock = threading.Lock()
        self._rx: re.Pattern[str] | None = None

    def register(self, *names: str) -> None:
        with self._lock:
            before = len(self._names)
            self._names.update(_fold(n) for n in names if n and len(n.strip()) >= 2)
            if len(self._names) != before:
                alts = sorted(self._names, key=len, reverse=True)
                self._rx = re.compile("|".join(re.escape(a) for a in alts))

    def redact(self, text: str) -> str:
        rx = self._rx
        if rx is None:
            return text
        text = unicodedata.normalize("NFC", text)
        folded = _fold(text)
        out, last = [], 0
        for m in rx.finditer(folded):
            out.append(text[last : m.start()])
            out.append("<redacted>")
            last = m.end()
        out.append(text[last:])
        return "".join(out)

    def filter(self, record: logging.LogRecord) -> bool:
        if self._rx is not None:
            record.msg = self.redact(record.getMessage())
            record.args = None
        return True


redactor = RedactingFilter()


def get_logger(name: str = PACKAGE) -> logging.Logger:
    """Logger under the package namespace with the redacting filter attached."""
    if name != PACKAGE and not name.startswith(PACKAGE + "."):
        name = f"{PACKAGE}.{name}"
    lg = logging.getLogger(name)
    if redactor not in lg.filters:
        lg.addFilter(redactor)
    return lg


logger = get_logger()
logger.addHandler(logging.NullHandler())
