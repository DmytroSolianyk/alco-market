"""Мінімальний клієнт Telegram Bot API з повагою до лімітів."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, chat_id: str, thread_id: str = "", timeout: int = 30) -> None:
        self.token = token
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.timeout = timeout
        self.session = requests.Session()

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = API.format(token=self.token, method=method)
        body = dict(payload)
        if self.thread_id:
            body.setdefault("message_thread_id", self.thread_id)

        for attempt in range(4):
            try:
                resp = self.session.post(url, data=body, timeout=self.timeout)
            except requests.RequestException as exc:
                log.warning("Telegram %s: мережева помилка (%s), спроба %d", method, exc, attempt + 1)
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 429:
                retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
                log.warning("Telegram rate limit, чекаю %ss", retry_after)
                time.sleep(retry_after + 1)
                continue

            data = resp.json() if resp.content else {}
            if resp.ok and data.get("ok"):
                return data.get("result", {})

            description = data.get("description", resp.text[:200])
            # 400 — це наша помилка (битий URL фото, задовгий текст): ретрай не поможе.
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                raise TelegramError(f"{method}: HTTP {resp.status_code} — {description}")
            log.warning("Telegram %s: HTTP %s — %s", method, resp.status_code, description)
            time.sleep(2 ** attempt)

        raise TelegramError(f"{method}: не вдалося після кількох спроб")

    # ------------------------------------------------------------------ API

    def get_me(self) -> dict[str, Any]:
        return self._call("getMe", {})

    def get_updates(self, offset: int = 0, timeout: int = 0) -> list[dict[str, Any]]:
        """timeout > 0 вмикає long polling — з'єднання висить до нової події."""
        url = API.format(token=self.token, method="getUpdates")
        params: dict[str, Any] = {}
        if offset:
            params["offset"] = offset
        if timeout:
            params["timeout"] = timeout
        # HTTP-таймаут має бути більшим за polling-таймаут, інакше рвемо самі себе.
        resp = self.session.get(url, params=params, timeout=self.timeout + timeout + 5)
        return resp.json().get("result", [])

    def set_my_commands(self, commands: list[tuple[str, str]]) -> dict[str, Any]:
        import json as _json

        payload = _json.dumps(
            [{"command": c, "description": d} for c, d in commands], ensure_ascii=False
        )
        return self._call("setMyCommands", {"commands": payload})

    def send_message(self, text: str, disable_notification: bool = False) -> dict[str, Any]:
        return self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
                "disable_notification": "true" if disable_notification else "false",
            },
        )

    def send_photo(self, photo_url: str, caption: str, button_url: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML",
        }
        if button_url:
            payload["reply_markup"] = (
                '{"inline_keyboard":[[{"text":"🛒 Відкрити в Сільпо","url":"%s"}]]}' % button_url
            )
        return self._call("sendPhoto", payload)
