from __future__ import annotations

import logging
from typing import TypedDict

import user_agents

logger = logging.getLogger("apps.telemetry")


class UAData(TypedDict):
    browser: str
    os: str
    device_type: str
    browser_version: str
    is_mobile: bool


_EMPTY: UAData = {
    "browser": "",
    "os": "",
    "device_type": "unknown",
    "browser_version": "",
    "is_mobile": False,
}


def parse_user_agent(ua_string: str) -> UAData:
    """
    Parse a User-Agent string into structured fields.
    Never raises — returns _EMPTY on any error.
    """
    if not ua_string or not ua_string.strip():
        return _EMPTY.copy()

    try:
        ua = user_agents.parse(ua_string)
    except Exception:
        logger.debug("user_agents.parse failed for ua=%r", ua_string[:100])
        return _EMPTY.copy()

    if ua.is_bot:
        device_type = "bot"
    elif ua.is_mobile:
        device_type = "mobile"
    elif ua.is_tablet:
        device_type = "tablet"
    elif ua.is_pc:
        device_type = "desktop"
    else:
        device_type = "unknown"

    return {
        "browser": (ua.browser.family or "")[:60],
        "os": (ua.os.family or "")[:60],
        "device_type": device_type,
        "browser_version": (ua.browser.version_string or "")[:20],
        "is_mobile": ua.is_mobile,
    }
