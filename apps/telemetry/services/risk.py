from __future__ import annotations

_BASE_SCORES: dict[str, int] = {
    "login_failed": 20,
    "login_blocked": 40,
    "account_locked": 60,
    "suspicious_login": 50,
}

_FACTOR_SCORES: dict[str, int] = {
    "new_device": 25,
    "new_ip": 10,
    "rapid_successive": 15,
    "unusual_hour": 10,
}


def compute_risk_score(
    event_type: str,
    success: bool = True,
    is_new_device: bool = False,
    risk_factors: list[str] | None = None,
) -> int:
    """
    Rule-based risk score 0–100.
    Designed to be extended: add factor rules to _FACTOR_SCORES or
    call an external ML model in the future without changing the signature.
    """
    score = _BASE_SCORES.get(event_type, 0)

    if not success:
        score += 10

    if is_new_device:
        score += _FACTOR_SCORES["new_device"]

    for factor in risk_factors or []:
        score += _FACTOR_SCORES.get(factor, 0)

    return min(score, 100)
