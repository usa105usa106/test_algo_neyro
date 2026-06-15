from __future__ import annotations

import json
from pathlib import Path
from cryptography.fernet import Fernet


class SecretStore:
    def __init__(self, secrets_dir: Path, state_dir: Path, env_key: str | None = None):
        self.secrets_dir = secrets_dir
        self.state_dir = state_dir
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.key_file = self.state_dir / "fernet.key"
        self.api_file = self.secrets_dir / "mexc_api.enc.json"
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

    def save_mexc_api(self, api_key: str, api_secret: str) -> dict:
        payload = {
            "api_key": api_key.strip(),
            "api_secret": api_secret.strip(),
        }
        token = self.fernet.encrypt(json.dumps(payload).encode("utf-8")).decode("utf-8")
        saved = {
            "encrypted": token,
            "mask": {
                "api_key": self.mask(payload["api_key"]),
                "api_secret": self.mask(payload["api_secret"]),
            },
        }
        self.api_file.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            self.api_file.chmod(0o600)
        except Exception:
            pass
        return saved["mask"]

    def load_mexc_api_mask(self) -> dict | None:
        if not self.api_file.exists():
            return None
        data = json.loads(self.api_file.read_text(encoding="utf-8"))
        return data.get("mask")

    def load_mexc_api(self) -> dict | None:
        if not self.api_file.exists():
            return None
        data = json.loads(self.api_file.read_text(encoding="utf-8"))
        decrypted = self.fernet.decrypt(data["encrypted"].encode("utf-8"))
        return json.loads(decrypted.decode("utf-8"))

    def clear(self) -> None:
        if self.api_file.exists():
            self.api_file.unlink()
