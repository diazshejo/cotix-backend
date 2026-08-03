import re


def sanitize_error_message(message):
    safe = str(message or "Error de integracion.")
    safe = re.sub(r"(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 ***", safe, flags=re.IGNORECASE)
    safe = re.sub(
        r"(authorization|api[-_ ]?key|token|password|secret|credentials?)(=|:)\s*[^,\s]+",
        r"\1\2 ***",
        safe,
        flags=re.IGNORECASE,
    )
    return safe[:500]
