"""WebUi 자격증명 — TOML 파일(username/password) 로드 + 강한 비밀번호 검증."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11 fallback
    import tomli as tomllib  # type: ignore

_MIN_LEN = 12
_PLACEHOLDERS = ("set-me", "changeme", "password", "admin")


@dataclass(frozen=True, repr=False)
class WebUiCredentials:
    username: str
    password: str

    def __repr__(self) -> str:
        return f"WebUiCredentials(username={self.username!r}, password='***')"


def load_credentials(
    path: str | Path,
    *,
    min_length: int = _MIN_LEN,
    forbidden_tokens: tuple | list = _PLACEHOLDERS,
) -> WebUiCredentials:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"web-ui credentials file not found: {path}")
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not username:
        raise ValueError("web-ui credentials: username required")
    if len(password) < min_length:
        raise ValueError(f"web-ui credentials: password must be >= {min_length} chars")
    low = password.lower()
    if any(token in low for token in forbidden_tokens):
        raise ValueError("web-ui credentials: password contains a placeholder/default token")
    return WebUiCredentials(username=username, password=password)
