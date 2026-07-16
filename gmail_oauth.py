from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import logging
import mimetypes
import re
import secrets
import time
import zipfile
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
OAUTH_SCOPES = ["openid", "email", GMAIL_SEND_SCOPE]

_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,}\.apps\.googleusercontent\.com$")


class GmailOAuthError(RuntimeError):
    pass


class GmailAttachmentTooLarge(GmailOAuthError):
    pass


class GmailArchiveChanged(GmailOAuthError):
    pass


class GmailSendUncertain(GmailOAuthError):
    """The request may have reached Gmail, so automatic retry is blocked."""


@dataclass(frozen=True)
class ArchiveIdentity:
    name: str
    size: int
    sha256: str

    @property
    def ledger_key(self) -> str:
        return f"{self.name}|{self.size}|{self.sha256}"


class GmailOAuthManager:
    """Google OAuth callback server + Gmail API sender.

    Google Client ID/Secret may come from an encrypted Telegram setup file or,
    for backward compatibility, from Coolify environment variables. OAuth tokens
    and sent-archive idempotency state persist in the named storage volume.
    """

    def __init__(self, settings: Any, secret_store: Any, logger: logging.Logger):
        self.settings = settings
        self.secret_store = secret_store
        self.logger = logger
        self._pending_states: dict[str, dict[str, Any]] = {}
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._bot: Any | None = None
        self._send_lock = asyncio.Lock()

    @staticmethod
    def validate_client_id(value: str) -> str:
        value = (value or "").strip()
        if not _CLIENT_ID_RE.fullmatch(value):
            raise GmailOAuthError(
                "Client ID выглядит неверно. Он должен оканчиваться на .apps.googleusercontent.com."
            )
        return value

    @staticmethod
    def validate_client_secret(value: str) -> str:
        value = (value or "").strip()
        if not (8 <= len(value) <= 512):
            raise GmailOAuthError("Client Secret выглядит неверно: слишком короткий или слишком длинный.")
        if any(ch.isspace() for ch in value):
            raise GmailOAuthError("Client Secret не должен содержать пробелы или переносы строк.")
        return value

    def save_client_credentials(self, client_id: str, client_secret: str) -> dict[str, str]:
        client_id = self.validate_client_id(client_id)
        client_secret = self.validate_client_secret(client_secret)
        # A refresh token is bound to the OAuth client that created it. Replacing
        # Client ID/Secret must therefore disconnect the previous Gmail token.
        self.secret_store.clear_gmail_oauth()
        mask = self.secret_store.save_gmail_client(client_id, client_secret)
        self.logger.info("Google OAuth client saved via Telegram client_id_mask=%s", mask.get("client_id"))
        return mask

    def clear_client_credentials(self) -> None:
        self.secret_store.clear_gmail_oauth()
        self.secret_store.clear_gmail_client()

    def _client_credentials(self) -> tuple[str, str, str]:
        stored = self.secret_store.load_gmail_client() or {}
        stored_id = str(stored.get("client_id") or "").strip()
        stored_secret = str(stored.get("client_secret") or "").strip()
        if stored_id and stored_secret:
            return stored_id, stored_secret, "telegram"
        env_id = str(self.settings.gmail_client_id or "").strip()
        env_secret = str(self.settings.gmail_client_secret or "").strip()
        if env_id and env_secret:
            return env_id, env_secret, "coolify"
        return "", "", "none"

    @property
    def client_source(self) -> str:
        return self._client_credentials()[2]

    @property
    def configured(self) -> bool:
        client_id, client_secret, _ = self._client_credentials()
        return bool(client_id and client_secret and self.settings.gmail_redirect_uri)

    @property
    def connected(self) -> bool:
        token = self.secret_store.load_gmail_oauth()
        return bool(token and token.get("refresh_token") and token.get("email") and self.configured)

    @property
    def account_email(self) -> str | None:
        token = self.secret_store.load_gmail_oauth()
        if not token:
            return None
        email_value = str(token.get("email") or "").strip()
        return email_value or None

    def status_text(self) -> str:
        if not self.settings.gmail_redirect_uri:
            return "нет callback URL Coolify"
        client_id, client_secret, source = self._client_credentials()
        if not (client_id and client_secret):
            return "нужно ввести Client ID/Secret в Telegram"
        email_value = self.account_email
        if email_value:
            return f"подключён: {email_value} (client: {source})"
        return f"OAuth-клиент сохранён ({source}), Gmail не подключён"

    def create_authorization_url(self, chat_id: int) -> str:
        client_id, client_secret, _ = self._client_credentials()
        if not (client_id and client_secret and self.settings.gmail_redirect_uri):
            raise GmailOAuthError(
                "Не сохранены Google Client ID/Secret или Coolify не создал публичный callback URL."
            )
        self._purge_expired_states()
        state = secrets.token_urlsafe(32)
        self._pending_states[state] = {
            "chat_id": int(chat_id),
            "expires_at": time.time() + 15 * 60,
        }
        params = {
            "client_id": client_id,
            "redirect_uri": self.settings.gmail_redirect_uri,
            "response_type": "code",
            "scope": " ".join(OAUTH_SCOPES),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    def disconnect(self) -> None:
        self.secret_store.clear_gmail_oauth()

    async def start_web_server(self, bot: Any) -> None:
        self._bot = bot
        if not self.settings.gmail_redirect_uri:
            self.logger.warning("Gmail OAuth web server disabled: Coolify public callback URL is missing.")
            return
        if not self.configured:
            self.logger.warning(
                "Gmail OAuth callback server starts without Google credentials; enter them through Telegram."
            )
        app = web.Application(client_max_size=1024 * 1024)
        app.router.add_get("/healthz", self._healthz)
        app.router.add_get("/gmail/callback", self._oauth_callback)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host=self.settings.gmail_oauth_listen_host,
            port=self.settings.gmail_oauth_listen_port,
        )
        await self._site.start()
        self.logger.info(
            "Gmail OAuth callback server started on %s:%s redirect=%s",
            self.settings.gmail_oauth_listen_host,
            self.settings.gmail_oauth_listen_port,
            self.settings.gmail_redirect_uri,
        )

    async def stop_web_server(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None
        self._bot = None

    async def describe_archive(self, archive_path: Path) -> ArchiveIdentity:
        return await asyncio.to_thread(self._describe_archive_sync, Path(archive_path))

    @staticmethod
    def _describe_archive_sync(archive_path: Path) -> ArchiveIdentity:
        if not archive_path.is_file():
            raise GmailOAuthError(f"Архив не найден: {archive_path}")
        if archive_path.suffix.lower() != ".zip":
            raise GmailOAuthError(f"Для Gmail ожидался ZIP, получен файл: {archive_path.name}")
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                broken = zf.testzip()
                if broken:
                    raise GmailOAuthError(f"ZIP повреждён внутри: {broken}")
        except zipfile.BadZipFile as exc:
            raise GmailOAuthError(f"ZIP повреждён: {archive_path.name}") from exc
        digest = hashlib.sha256()
        with archive_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        stat = archive_path.stat()
        return ArchiveIdentity(name=archive_path.name, size=int(stat.st_size), sha256=digest.hexdigest())

    @staticmethod
    def _assert_same_archive(expected: ArchiveIdentity, actual: ArchiveIdentity) -> None:
        if expected != actual:
            raise GmailArchiveChanged(
                "ZIP изменился после отправки в Telegram; Gmail-отправка остановлена, чтобы не отправить другой архив. "
                f"Telegram={expected.name}/{expected.size}/{expected.sha256[:12]}, "
                f"текущий={actual.name}/{actual.size}/{actual.sha256[:12]}."
            )

    async def send_archive(
        self,
        archive_path: Path,
        subject_prefix: str = "ChatGPT Scan",
        *,
        expected_identity: ArchiveIdentity | None = None,
        telegram_filename: str | None = None,
    ) -> dict[str, Any]:
        archive_path = Path(archive_path)
        if not self.configured:
            raise GmailOAuthError("Gmail OAuth не настроен: введи Google Client ID/Secret через Telegram.")
        token = self.secret_store.load_gmail_oauth()
        if not token or not token.get("refresh_token"):
            raise GmailOAuthError("Gmail не подключён. Нажми кнопку «Подключить Gmail».")

        actual_identity = await self.describe_archive(archive_path)
        expected = expected_identity or actual_identity
        self._assert_same_archive(expected, actual_identity)
        delivered_name = str(telegram_filename or expected.name).strip()
        if delivered_name != expected.name:
            raise GmailArchiveChanged(
                f"Telegram показал имя {delivered_name}, а локальный ZIP называется {expected.name}; отправка остановлена."
            )

        max_bytes = int(self.settings.gmail_max_attachment_mb * 1024 * 1024)
        if actual_identity.size > max_bytes:
            raise GmailAttachmentTooLarge(
                f"{actual_identity.name}: {actual_identity.size / 1024 / 1024:.1f} MB; лимит бота для Gmail "
                f"{self.settings.gmail_max_attachment_mb} MB."
            )

        async with self._send_lock:
            # Re-hash under the send lock: another coroutine/file cleanup must not
            # replace the ZIP between Telegram delivery and Gmail MIME building.
            locked_identity = await self.describe_archive(archive_path)
            self._assert_same_archive(expected, locked_identity)
            sender = str(token.get("email") or "").strip()
            recipient = str(self.settings.gmail_send_to or sender).strip()
            if not sender or not recipient:
                raise GmailOAuthError("Не удалось определить Gmail-адрес отправителя/получателя.")
            ledger_key = f"{recipient.lower()}|{locked_identity.ledger_key}"
            ledger = self.secret_store.load_gmail_send_ledger()
            archives = ledger.setdefault("archives", {})
            existing = archives.get(ledger_key)
            if isinstance(existing, dict) and existing.get("status") in {"sent", "sending", "uncertain"}:
                self.logger.warning(
                    "Gmail duplicate blocked file=%s sha256=%s recipient=%s status=%s",
                    locked_identity.name,
                    locked_identity.sha256,
                    recipient,
                    existing.get("status"),
                )
                return {
                    "duplicate_skipped": True,
                    "status": existing.get("status"),
                    "archive": asdict(locked_identity),
                    "message_id": existing.get("gmail_message_id"),
                }

            access_token = await self._valid_access_token(token)
            raw = await asyncio.to_thread(
                self._build_raw_message,
                archive_path,
                sender,
                recipient,
                subject_prefix,
                locked_identity,
            )
            now = time.time()
            archives[ledger_key] = {
                "status": "sending",
                "archive": asdict(locked_identity),
                "sender": sender,
                "recipient": recipient,
                "started_at": now,
                "updated_at": now,
            }
            self._prune_ledger(ledger)
            self.secret_store.save_gmail_send_ledger(ledger)

            timeout = aiohttp.ClientTimeout(total=120, connect=20, sock_read=90)
            try:
                payload = await self._post_gmail_raw(raw, access_token, token, timeout)
            except GmailSendUncertain as exc:
                item = archives[ledger_key]
                item.update({"status": "uncertain", "updated_at": time.time(), "error": str(exc)[:1000]})
                self.secret_store.save_gmail_send_ledger(ledger)
                raise
            except Exception as exc:
                # Explicit 4xx/configuration failures are safe to retry after fixing.
                archives.pop(ledger_key, None)
                self.secret_store.save_gmail_send_ledger(ledger)
                raise

            item = archives[ledger_key]
            item.update(
                {
                    "status": "sent",
                    "updated_at": time.time(),
                    "sent_at": time.time(),
                    "gmail_message_id": payload.get("id") if isinstance(payload, dict) else None,
                    "gmail_thread_id": payload.get("threadId") if isinstance(payload, dict) else None,
                }
            )
            self.secret_store.save_gmail_send_ledger(ledger)
            self.logger.info(
                "Gmail archive sent exact_file=%s size=%s sha256=%s from=%s to=%s message_id=%s",
                locked_identity.name,
                locked_identity.size,
                locked_identity.sha256,
                sender,
                recipient,
                item.get("gmail_message_id"),
            )
            result = payload if isinstance(payload, dict) else {}
            result.update({"archive": asdict(locked_identity), "duplicate_skipped": False})
            return result

    async def _post_gmail_raw(
        self,
        raw: str,
        access_token: str,
        token: dict[str, Any],
        timeout: aiohttp.ClientTimeout,
    ) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    GMAIL_SEND_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"raw": raw},
                ) as response:
                    payload = await self._response_json(response)
                    if response.status == 401:
                        # 401 is a definitive rejection; refreshing and retrying once is safe.
                        token["access_token"] = ""
                        token["expires_at"] = 0
                        self.secret_store.save_gmail_oauth(token)
                        access_token = await self._valid_access_token(token)
                        async with session.post(
                            GMAIL_SEND_URL,
                            headers={"Authorization": f"Bearer {access_token}"},
                            json={"raw": raw},
                        ) as retry_response:
                            payload = await self._response_json(retry_response)
                            if retry_response.status == 408 or retry_response.status >= 500:
                                raise GmailSendUncertain(
                                    f"Gmail API HTTP {retry_response.status}; письмо могло быть принято. Автоповтор заблокирован."
                                )
                            if retry_response.status >= 300:
                                raise GmailOAuthError(
                                    f"Gmail API send failed HTTP {retry_response.status}: {payload}"
                                )
                    elif response.status == 408 or response.status >= 500:
                        raise GmailSendUncertain(
                            f"Gmail API HTTP {response.status}; письмо могло быть принято. Автоповтор заблокирован."
                        )
                    elif response.status >= 300:
                        raise GmailOAuthError(f"Gmail API send failed HTTP {response.status}: {payload}")
                    return payload if isinstance(payload, dict) else {}
        except GmailOAuthError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise GmailSendUncertain(
                "Соединение с Gmail оборвалось во время отправки; неизвестно, принято ли письмо. "
                "Чтобы не создать дубль, этот ZIP автоматически повторно не отправляется."
            ) from exc

    async def send_test(self) -> dict[str, Any]:
        if not self.configured:
            raise GmailOAuthError("Google Client ID/Secret не сохранены.")
        token = self.secret_store.load_gmail_oauth()
        if not token or not token.get("refresh_token"):
            raise GmailOAuthError("Gmail не подключён.")
        access_token = await self._valid_access_token(token)
        sender = str(token.get("email") or "").strip()
        recipient = str(self.settings.gmail_send_to or sender).strip()
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = sender
        message["Subject"] = "ChatGPT Scan Bot — Gmail test"
        message.set_content(
            "Gmail подключён. ZIP-архивы будут отправляться через Gmail API и всегда доступны в папке «Отправленные»."
        )
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        timeout = aiohttp.ClientTimeout(total=60, connect=20, sock_read=40)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    GMAIL_SEND_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"raw": raw},
                ) as response:
                    payload = await self._response_json(response)
                    if response.status >= 300:
                        raise GmailOAuthError(f"Gmail API test failed HTTP {response.status}: {payload}")
                    return payload if isinstance(payload, dict) else {}
        except GmailOAuthError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise GmailOAuthError(f"Сетевая ошибка тестового письма: {exc}") from exc

    async def _healthz(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "gmail_configured": self.configured,
                "gmail_connected": self.connected,
                "gmail_redirect_uri": self.settings.gmail_redirect_uri or None,
                "gmail_client_source": self.client_source,
            }
        )

    async def _oauth_callback(self, request: web.Request) -> web.Response:
        self._purge_expired_states()
        state = str(request.query.get("state") or "")
        error = str(request.query.get("error") or "")
        pending = self._pending_states.pop(state, None) if state else None
        if not pending:
            return self._html_response(
                "Ошибка авторизации",
                "Ссылка устарела или state не совпал. Вернись в Telegram и нажми «Подключить Gmail» ещё раз.",
                status=400,
            )
        chat_id = int(pending["chat_id"])
        if error:
            await self._notify(chat_id, f"❌ Gmail не подключён: Google вернул {error}.")
            return self._html_response("Авторизация отменена", f"Google вернул: {html.escape(error)}", status=400)

        code = str(request.query.get("code") or "")
        if not code:
            await self._notify(chat_id, "❌ Gmail не подключён: callback пришёл без authorization code.")
            return self._html_response("Ошибка авторизации", "Google не вернул authorization code.", status=400)

        try:
            token_payload = await self._exchange_code(code)
            old = self.secret_store.load_gmail_oauth() or {}
            refresh_token = token_payload.get("refresh_token") or old.get("refresh_token")
            if not refresh_token:
                raise GmailOAuthError(
                    "Google не вернул refresh_token. Отключи доступ приложения в аккаунте Google и подключи заново."
                )
            access_token = str(token_payload.get("access_token") or "")
            user = await self._load_userinfo(access_token)
            email_value = str(user.get("email") or "").strip()
            if not email_value:
                raise GmailOAuthError("Google не вернул email подключённого аккаунта.")
            granted_scope = str(token_payload.get("scope") or "")
            if GMAIL_SEND_SCOPE not in granted_scope.split():
                raise GmailOAuthError("Не выдано разрешение gmail.send.")
            stored = {
                "email": email_value,
                "refresh_token": refresh_token,
                "access_token": access_token,
                "expires_at": time.time() + int(token_payload.get("expires_in") or 3600),
                "scope": granted_scope,
                "token_type": token_payload.get("token_type", "Bearer"),
                "connected_at": time.time(),
            }
            self.secret_store.save_gmail_oauth(stored)
            await self._notify(
                chat_id,
                f"✅ Gmail подключён: {email_value}\n"
                "ZIP после успешной отправки в Telegram будет уходить один раз и появляться в Gmail → Отправленные.",
            )
            return self._html_response(
                "Gmail подключён",
                f"Аккаунт {html.escape(email_value)} подключён. Эту страницу можно закрыть и вернуться в Telegram.",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Gmail OAuth callback failed: %s", exc)
            await self._notify(chat_id, f"❌ Gmail OAuth ошибка: {exc}")
            return self._html_response("Ошибка Gmail OAuth", html.escape(str(exc)), status=500)

    async def _exchange_code(self, code: str) -> dict[str, Any]:
        client_id, client_secret, _ = self._client_credentials()
        if not client_id or not client_secret:
            raise GmailOAuthError("Google Client ID/Secret удалены; введи их заново в Telegram.")
        timeout = aiohttp.ClientTimeout(total=60, connect=20, sock_read=40)
        data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": self.settings.gmail_redirect_uri,
            "grant_type": "authorization_code",
        }
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(GOOGLE_TOKEN_URL, data=data) as response:
                payload = await self._response_json(response)
                if response.status >= 300:
                    raise GmailOAuthError(f"Token exchange failed HTTP {response.status}: {payload}")
                if not isinstance(payload, dict):
                    raise GmailOAuthError("Token exchange returned non-JSON response.")
                return payload

    async def _load_userinfo(self, access_token: str) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=30, connect=15, sock_read=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            ) as response:
                payload = await self._response_json(response)
                if response.status >= 300 or not isinstance(payload, dict):
                    raise GmailOAuthError(f"Google userinfo failed HTTP {response.status}: {payload}")
                return payload

    async def _valid_access_token(self, token: dict[str, Any]) -> str:
        access_token = str(token.get("access_token") or "")
        expires_at = float(token.get("expires_at") or 0)
        if access_token and expires_at > time.time() + 90:
            return access_token
        refresh_token = str(token.get("refresh_token") or "")
        if not refresh_token:
            raise GmailOAuthError("Refresh token отсутствует; требуется повторное подключение Gmail.")
        client_id, client_secret, _ = self._client_credentials()
        if not client_id or not client_secret:
            raise GmailOAuthError("Google Client ID/Secret отсутствуют; введи их заново в Telegram.")
        timeout = aiohttp.ClientTimeout(total=60, connect=20, sock_read=40)
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(GOOGLE_TOKEN_URL, data=data) as response:
                payload = await self._response_json(response)
                if response.status >= 300 or not isinstance(payload, dict):
                    raise GmailOAuthError(
                        f"Refresh token failed HTTP {response.status}: {payload}. Подключи Gmail заново."
                    )
        token["access_token"] = str(payload.get("access_token") or "")
        token["expires_at"] = time.time() + int(payload.get("expires_in") or 3600)
        if payload.get("scope"):
            token["scope"] = payload["scope"]
        self.secret_store.save_gmail_oauth(token)
        return token["access_token"]

    @staticmethod
    def _build_raw_message(
        archive_path: Path,
        sender: str,
        recipient: str,
        subject_prefix: str,
        identity: ArchiveIdentity,
    ) -> str:
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = sender
        message["Subject"] = f"{subject_prefix}: {identity.name}"
        message["Message-ID"] = f"<chatgpt-scan-{identity.sha256[:40]}@archive.local>"
        message["X-ChatGPT-Archive-Name"] = identity.name
        message["X-ChatGPT-Archive-SHA256"] = identity.sha256
        message.set_content(
            "Автоматический архив ChatGPT Scan Bot.\n"
            "Ищи это письмо в Gmail → Отправленные.\n"
            f"Файл: {identity.name}\n"
            f"Размер: {identity.size} bytes\n"
            f"SHA-256: {identity.sha256}\n"
        )
        mime_type, _ = mimetypes.guess_type(identity.name)
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "zip"
        message.add_attachment(
            archive_path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=identity.name,
        )
        return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

    @staticmethod
    def _prune_ledger(ledger: dict[str, Any], limit: int = 1000) -> None:
        archives = ledger.get("archives")
        if not isinstance(archives, dict) or len(archives) <= limit:
            return
        ordered = sorted(
            archives.items(),
            key=lambda item: float(item[1].get("updated_at", 0)) if isinstance(item[1], dict) else 0,
            reverse=True,
        )
        ledger["archives"] = dict(ordered[:limit])

    async def _notify(self, chat_id: int, text: str) -> None:
        if self._bot is None:
            self.logger.warning("Could not notify Telegram chat=%s: bot is not attached", chat_id)
            return
        try:
            await self._bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Could not notify Telegram about Gmail OAuth: %s", exc)

    def _purge_expired_states(self) -> None:
        now = time.time()
        expired = [key for key, item in self._pending_states.items() if float(item.get("expires_at", 0)) <= now]
        for key in expired:
            self._pending_states.pop(key, None)

    @staticmethod
    async def _response_json(response: aiohttp.ClientResponse) -> Any:
        try:
            return await response.json(content_type=None)
        except Exception:
            return {"text": (await response.text())[:2000]}

    @staticmethod
    def _html_response(title: str, message: str, status: int = 200) -> web.Response:
        body = f"""<!doctype html>
<html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>{html.escape(title)}</title><style>body{{font-family:system-ui,sans-serif;max-width:720px;margin:60px auto;padding:0 20px;line-height:1.5}}.card{{border:1px solid #ddd;border-radius:16px;padding:24px}}</style></head>
<body><div class=\"card\"><h1>{html.escape(title)}</h1><p>{message}</p></div></body></html>"""
        return web.Response(text=body, status=status, content_type="text/html")
