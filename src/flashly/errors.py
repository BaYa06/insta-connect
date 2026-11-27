"""Meta Graph API error model. Messages are always masked."""
from __future__ import annotations

from .masking import mask

TOKEN_CODES = {190}
RATE_LIMIT_CODES = {4, 17, 32, 613, 80001, 80002, 80003, 80004, 80005, 80006, 80008, 80009, 80014}
TRANSIENT_CODES = {1, 2}
PERMISSION_CODES = {10, *range(200, 300)}
INVALID_REQUEST_CODES = {100}

_TOKEN_SUBCODES = {
    458: "the app is not authorized by the user",
    459: "the user must complete a Facebook checkpoint",
    460: "the user changed their password; the token was invalidated",
    463: "the token has expired",
    464: "the user's session is not confirmed",
    467: "the token is invalid",
}


class GraphAPIError(Exception):
    category = "api_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        subcode: int | None = None,
        http_status: int | None = None,
        error_type: str | None = None,
        fbtrace_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.message = mask(message)
        self.code = code
        self.subcode = subcode
        self.http_status = http_status
        self.error_type = error_type
        self.fbtrace_id = fbtrace_id
        self.retry_after = retry_after
        super().__init__(self.describe())

    def describe(self) -> str:
        bits = [f"[{self.category}]"]
        if self.code is not None:
            bits.append(f"code={self.code}")
        if self.subcode is not None:
            bits.append(f"subcode={self.subcode}")
        if self.http_status is not None:
            bits.append(f"http={self.http_status}")
        return f"{' '.join(bits)} {self.message}"

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "code": self.code,
            "subcode": self.subcode,
            "http_status": self.http_status,
            "message": self.message,
            "retryable": self.retryable,
        }


class TokenError(GraphAPIError):
    category = "token_invalid_or_expired"


class RateLimitError(GraphAPIError):
    category = "rate_limited"
    retryable = True


class TransientError(GraphAPIError):
    category = "transient"
    retryable = True


class GraphPermissionError(GraphAPIError):
    category = "permission_denied"


class InvalidRequestError(GraphAPIError):
    """Code 100: bad parameter, unsupported metric/field, etc."""

    category = "invalid_request"


class NetworkError(GraphAPIError):
    category = "network"
    retryable = True


class ConfigError(Exception):
    pass


def parse_error(status: int, body: object, headers: dict | None = None) -> GraphAPIError:
    """Build the most specific exception from a Graph API error response."""
    err = body.get("error") if isinstance(body, dict) else None
    retry_after = None
    if headers:
        try:
            retry_after = float(headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
    if not isinstance(err, dict):
        cls = TransientError if status >= 500 else GraphAPIError
        return cls(f"HTTP {status} without a Graph error body", http_status=status,
                   retry_after=retry_after)

    code, sub = err.get("code"), err.get("error_subcode")
    msg = str(err.get("message", "Unknown Graph API error"))
    kw = dict(code=code, subcode=sub, http_status=status, error_type=err.get("type"),
              fbtrace_id=err.get("fbtrace_id"), retry_after=retry_after)

    if code in TOKEN_CODES:
        reason = _TOKEN_SUBCODES.get(sub, "the token is expired or invalid")
        return TokenError(f"Access token problem: {reason}. Re-issue or refresh the "
                          f"token. Meta says: {msg}", **kw)
    if code in RATE_LIMIT_CODES:
        return RateLimitError(f"Rate limit reached. Retry later. Meta says: {msg}", **kw)
    if code in PERMISSION_CODES:
        return GraphPermissionError(f"Missing permission or access. Meta says: {msg}", **kw)
    if code in INVALID_REQUEST_CODES:
        return InvalidRequestError(msg, **kw)
    if code in TRANSIENT_CODES or status >= 500:
        return TransientError(msg, **kw)
    return GraphAPIError(msg, **kw)
