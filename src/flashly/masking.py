"""Secret masking. Applied to logs, exception messages and tool errors."""
from __future__ import annotations

import logging
import re
import threading

_MASK = "***"

# key=value (query strings, form bodies) and "key": "value" (JSON)
_KV = re.compile(
    r"(?i)\b(access_token|client_secret|input_token|fb_exchange_token|"
    r"appsecret_proof|app_secret|api_key|bearer_token)=([^&\s\"'#]+)"
)
_JSON_KV = re.compile(
    r"(?i)(\"(?:access_token|client_secret|input_token|fb_exchange_token|"
    r"app_secret|api_key)\"\s*:\s*\")([^\"]*)(\")"
)
_BEARER = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=|-]+")
_APP_TOKEN = re.compile(r"\b\d{8,}\|[A-Za-z0-9_-]{8,}\b")  # APP_ID|APP_SECRET
_META_TOKEN = re.compile(r"\bEAA[A-Za-z0-9]{10,}\b")  # Meta user/page tokens

_secrets: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str | None) -> None:
    """Remember a literal secret so it is masked wherever it appears."""
    if value and len(value) >= 6:
        with _lock:
            _secrets.add(value)


def mask(text: object) -> str:
    s = text if isinstance(text, str) else str(text)
    with _lock:
        literals = sorted(_secrets, key=len, reverse=True)
    for secret in literals:
        s = s.replace(secret, _MASK)
    s = _KV.sub(lambda m: f"{m.group(1)}={_MASK}", s)
    s = _JSON_KV.sub(lambda m: f"{m.group(1)}{_MASK}{m.group(3)}", s)
    s = _BEARER.sub(lambda m: f"{m.group(1)} {_MASK}", s)
    s = _APP_TOKEN.sub(_MASK, s)
    s = _META_TOKEN.sub(_MASK, s)
    return s


class MaskingFormatter(logging.Formatter):
    """Masks the fully rendered record, including tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request URLs (with access_token) at INFO.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
