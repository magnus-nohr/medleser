"""Error types with Norwegian, user-friendly messages.

Every error that can reach the user carries a plain-language Norwegian
`message` (shown as the primary text) and optional technical `details`
(shown only behind «Vis detaljer» in the UI). Never put API keys or full
document text in either field.
"""

from __future__ import annotations


class UserFacingError(Exception):
    """An error whose message can be shown directly to the user."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "error",
        details: str | None = None,
        status: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details
        self.status = status

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class DocumentImportError(UserFacingError):
    """The document could not be read (unsupported, damaged, protected, empty …)."""


class CancelledError(UserFacingError):
    """The user cancelled a long-running operation."""

    def __init__(self, message: str = "Arbeidet ble avbrutt.") -> None:
        super().__init__(message, code="cancelled", status=409)
