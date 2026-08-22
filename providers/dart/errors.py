"""OpenDART application-status semantics without main-application types."""

from enum import StrEnum


class DartErrorKind(StrEnum):
    AUTH = "AUTH"
    IP = "IP"
    NO_RESULT = "NO_RESULT"
    RATE_LIMIT = "RATE_LIMIT"
    UPSTREAM = "UPSTREAM"
    INVALID_REQUEST = "INVALID_REQUEST"


_STATUS_ERRORS = {
    "010": (DartErrorKind.AUTH, False),
    "011": (DartErrorKind.AUTH, False),
    "901": (DartErrorKind.AUTH, False),
    "012": (DartErrorKind.IP, False),
    "013": (DartErrorKind.NO_RESULT, False),
    "014": (DartErrorKind.NO_RESULT, False),
    "020": (DartErrorKind.RATE_LIMIT, True),
    "800": (DartErrorKind.UPSTREAM, True),
    "900": (DartErrorKind.UPSTREAM, True),
    "100": (DartErrorKind.INVALID_REQUEST, False),
    "101": (DartErrorKind.INVALID_REQUEST, False),
}


def classify_status(status: object) -> tuple[DartErrorKind, bool]:
    normalized = str(status or "")
    try:
        return _STATUS_ERRORS[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown OpenDART status: {normalized!r}") from exc


def require_success(raw: dict) -> None:
    if raw.get("status") == "000":
        return
    kind, retryable = classify_status(raw.get("status"))
    raise ValueError(f"OpenDART error: {kind.value}; retryable={retryable}")
