"""Identity records used by the HTTP API."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class User:
    id: str
    username: str
    is_root: bool
    created_at: str
    disabled: bool = False

    def public_dict(self) -> dict[str, str | bool]:
        return {
            "id": self.id,
            "username": self.username,
            "is_root": self.is_root,
            "created_at": self.created_at,
            "disabled": self.disabled,
        }
