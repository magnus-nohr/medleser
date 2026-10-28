"""Secure storage of API keys in Windows Credential Manager (via ``keyring``).

Keys are never written to SQLite, project files, logs or the frontend.
"""

from __future__ import annotations

import logging
from typing import Optional

_LOG = logging.getLogger(__name__)
SERVICE = "Medleser"


class SecretStoreError(Exception):
    pass


class SecretStore:
    def __init__(self, service: str = SERVICE) -> None:
        self.service = service

    @staticmethod
    def _username(provider_id: str) -> str:
        return f"{provider_id}-api-key"

    def get(self, provider_id: str) -> Optional[str]:
        import keyring

        try:
            return keyring.get_password(self.service, self._username(provider_id))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Could not read secret for %s: %s", provider_id, type(exc).__name__)
            return None

    def set(self, provider_id: str, secret: str) -> None:
        import keyring

        try:
            keyring.set_password(self.service, self._username(provider_id), secret)
        except Exception as exc:  # noqa: BLE001
            raise SecretStoreError(type(exc).__name__) from exc

    def delete(self, provider_id: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.service, self._username(provider_id))
        except PasswordDeleteError:
            pass
        except Exception as exc:  # noqa: BLE001
            raise SecretStoreError(type(exc).__name__) from exc

    def has(self, provider_id: str) -> bool:
        return bool(self.get(provider_id))

    def backend_name(self) -> str:
        import keyring

        try:
            return type(keyring.get_keyring()).__name__
        except Exception:  # noqa: BLE001
            return "unknown"
