import logging
import re


SENSITIVE_PATTERNS = (
    re.compile(r"(password|token|secret|authorization|access|refresh)=([^&\s]+)", re.IGNORECASE),
    re.compile(r"(authorization\s*:\s*)(bearer|basic)\s+([^\s,;]+)", re.IGNORECASE),
    re.compile(r"(api[-_ ]?key\s*:\s*)([^\s,;]+)", re.IGNORECASE),
)


class RedactSensitiveFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        for pattern in SENSITIVE_PATTERNS:
            if pattern.groups >= 3:
                message = pattern.sub(r"\1\2 ***", message)
            else:
                message = pattern.sub(r"\1=***", message)
        record.msg = message
        record.args = ()
        return True
