"""Stockage des identifiants iCloud dans le Gestionnaire d'identification Windows (via keyring).

L'identifiant Apple est conservé sous la clé « __apple_id__ » ; le mot de passe
spécifique à l'app est conservé sous la clé = identifiant Apple. Le mot de passe
n'est jamais journalisé.
"""

from __future__ import annotations

import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

log = logging.getLogger(__name__)

SERVICE = "amphi-icloud"
_APPLE_ID_KEY = "__apple_id__"


def _get(username: str) -> str | None:
    return keyring.get_password(SERVICE, username)


def _delete(username: str) -> None:
    try:
        keyring.delete_password(SERVICE, username)
    except PasswordDeleteError:
        pass  # entrée absente : rien à supprimer


def get_credentials() -> tuple[str, str] | None:
    """Renvoie (identifiant Apple, mot de passe spécifique) ou None si incomplet / erreur."""
    try:
        apple_id = _get(_APPLE_ID_KEY)
        if not apple_id:
            return None
        password = _get(apple_id)
        if not password:
            return None
        return apple_id, password
    except KeyringError as exc:
        log.warning("Lecture du trousseau Windows impossible : %s", exc)
        return None


def set_credentials(apple_id: str, password: str) -> None:
    """Enregistre les identifiants ; supprime l'ancien mot de passe si l'identifiant a changé."""
    apple_id = apple_id.strip()
    password = password.strip()
    try:
        old_apple_id = _get(_APPLE_ID_KEY)
        if old_apple_id and old_apple_id != apple_id:
            _delete(old_apple_id)
        keyring.set_password(SERVICE, _APPLE_ID_KEY, apple_id)
        keyring.set_password(SERVICE, apple_id, password)
    except KeyringError as exc:
        log.warning("Écriture dans le trousseau Windows impossible : %s", exc)
        raise


def clear_credentials() -> None:
    """Supprime l'identifiant Apple et le mot de passe associé."""
    try:
        apple_id = _get(_APPLE_ID_KEY)
        if apple_id:
            _delete(apple_id)
        _delete(_APPLE_ID_KEY)
    except KeyringError as exc:
        log.warning("Suppression des identifiants iCloud impossible : %s", exc)
