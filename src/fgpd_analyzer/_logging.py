"""Package logger. Log records may carry ids, counts, hashes and timings only.

`RedactingFilter` is a second line of defence: every original username and
`extra_names` entry seen during preprocessing is registered here, and any
occurrence in a log message is replaced by ``<redacted>``.
"""

from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger("fgpd_analyzer")
logger.addHandler(logging.NullHandler())


class RedactingFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._names: set[str] = set()
        self._lock = threading.Lock()
        self._rx: re.Pattern[str] | None = None

    def register(self, *names: str) -> None:
        with self._lock:
            before = len(self._names)
            self._names.update(n for n in names if n and len(n) >= 2)
            if len(self._names) != before:
                alts = sorted(self._names, key=len, reverse=True)
                self._rx = re.compile("|".join(re.escape(a) for a in alts), re.IGNORECASE)

    def redact(self, text: str) -> str:
        rx = self._rx
        return rx.sub("<redacted>", text) if rx else text

    def filter(self, record: logging.LogRecord) -> bool:
        if self._rx is not None:
            record.msg = self.redact(record.getMessage())
            record.args = None
        return True


redactor = RedactingFilter()
logger.addFilter(redactor)
