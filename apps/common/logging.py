import logging
import re


SENSITIVE_PATTERNS = (
    re.compile(r"(password|token|secret|authorization|access|refresh)=([^&\s]+)", re.IGNORECASE),
)


class RedactSensitiveFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        for pattern in SENSITIVE_PATTERNS:
            message = pattern.sub(r"\1=***", message)
        record.msg = message
        record.args = ()
        return True
