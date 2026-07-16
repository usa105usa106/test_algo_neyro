from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


class SecretStore:
    def __init__(self, secrets_dir: Path, state_dir: Path, env_key: str | None = None):
        self.secrets_dir = secrets_dir
        self.state_dir = state_dir
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.key_file = self.state_dir / "fernet.key"
        self.api_file = self.secrets_dir / "mexc_api.enc.json"
        self.gmail_file = self.secrets_dir / "gmail_oauth.enc.json"
        self.gmail_client_file = self.secrets_dir / "gmail_client.enc.json"
        self.gmail_sent_file = self.state_dir / "gmail_sent_archives.json"
        self.fernet = Fernet(self._load_or_create_key(env_key))

    def _load_or_create_key(self, env_key: str | None) -> bytes:
        if env_key:
            return env_key.encode("utf-8")
        if self.key_file.exists():
            return self.key_file.read_bytes().strip()
        key = Fernet.generate_key()
        self.key_file.write_bytes(key)
        try:
            self.key_file.chmod(0o600)
        except Exception:
            pass
        return key

    @staticmethod
    def mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return value[:2] + "****"
        return value[:4] + "****" + value[-4:]

    @staticmethod
    def _atomic_write_json(path: Path, payload: Any, *, mode: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        if mode is not None:
            try:
                path.chmod(mode)
            except Exception:
                pass

    def _save_encrypted(self, path: Path, payload: dict[str, Any], mask: dict[str, Any]) -> dict[str, Any]:
        token = self.fernet.encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("utf-8")
        saved = {"encrypted": token, "mask": mask}
        self._atomic_write_json(path, saved, mode=0o600)
        return mask

    def _load_encrypted(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        decrypted = self.fernet.decrypt(data["encrypted"].encode("utf-8"))
        payload = json.loads(decrypted.decode("utf-8"))
        return payload if isinstance(payload, dict) else None

    def save_mexc_api(self, api_key: str, api_secret: str) -> dict:
        payload = {
            "api_key": api_key.strip(),
            "api_secret": api_secret.strip(),
        }
        return self._save_encrypted(
            self.api_file,
            payload,
            {
                "api_key": self.mask(payload["api_key"]),
                "api_secret": self.mask(payload["api_secret"]),
            },
        )

    def load_mexc_api_mask(self) -> dict | None:
        if not self.api_file.exists():
            return None
        data = json.loads(self.api_file.read_text(encoding="utf-8"))
        return data.get("mask")

    def load_mexc_api(self) -> dict | None:
        return self._load_encrypted(self.api_file)

    def save_gmail_client(self, client_id: str, client_secret: str) -> dict[str, str]:
        payload = {
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
        }
        return self._save_encrypted(
            self.gmail_client_file,
            payload,
            {
                "client_id": self.mask(payload["client_id"]),
                "client_secret": self.mask(payload["client_secret"]),
            },
        )

    def load_gmail_client(self) -> dict[str, Any] | None:
        return self._load_encrypted(self.gmail_client_file)

    def load_gmail_client_mask(self) -> dict[str, str] | None:
        if not self.gmail_client_file.exists():
            return None
        data = json.loads(self.gmail_client_file.read_text(encoding="utf-8"))
        mask = data.get("mask")
        return mask if isinstance(mask, dict) else None

    def clear_gmail_client(self) -> None:
        if self.gmail_client_file.exists():
            self.gmail_client_file.unlink()

    def save_gmail_oauth(self, payload: dict) -> None:
        self._save_encrypted(
            self.gmail_file,
            payload,
            {"email": payload.get("email", "")},
        )

    def load_gmail_oauth(self) -> dict | None:
        return self._load_encrypted(self.gmail_file)

    def clear_gmail_oauth(self) -> None:
        if self.gmail_file.exists():
            self.gmail_file.unlink()

    def load_gmail_send_ledger(self) -> dict[str, Any]:
        if not self.gmail_sent_file.exists():
            return {"version": 1, "archives": {}}
        try:
            payload = json.loads(self.gmail_sent_file.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "archives": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "archives": {}}
        archives = payload.get("archives")
        if not isinstance(archives, dict):
            payload["archives"] = {}
        payload.setdefault("version", 1)
        return payload

    def save_gmail_send_ledger(self, payload: dict[str, Any]) -> None:
        self._atomic_write_json(self.gmail_sent_file, payload, mode=0o600)

    def clear(self) -> None:
        # /reset historically clears only optional MEXC API metadata. Gmail OAuth,
        # Google client credentials and the duplicate ledger intentionally survive
        # ordinary bot resets and Coolify redeploys.
        if self.api_file.exists():
            self.api_file.unlink()
