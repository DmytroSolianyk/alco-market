"""Слухач команд у Telegram (long polling)."""
from __future__ import annotations

import logging
import threading
import time

from . import catalog, formatting
from .candidate import branches_label, group_identical
from .config import Config
from .silpo import SilpoClient
from .state import State
from .telegram import TelegramClient, TelegramError

log = logging.getLogger(__name__)

# Скільки секунд ігнорувати повторну ту саму команду в тому самому чаті.
COOLDOWN_SECONDS = 5


class CommandBot:
    def __init__(self, cfg: Config, stop_event: threading.Event) -> None:
        self.cfg = cfg
        self.stop = stop_event
        self.silpo = SilpoClient(timeout=cfg.request_timeout, image_size=cfg.image_size)
        self.state = State(cfg.state_path) if cfg.track_history else None
        self.lookup = catalog.PromoLookup(
            cfg, self.silpo, self.state.conn if self.state else None
        )
        self._last_handled: dict[tuple[int, str], float] = {}

    # ------------------------------------------------------------- helpers

    def _client_for(self, chat_id: int | str) -> TelegramClient:
        # Відповідаємо в той чат, звідки прийшла команда, а не в налаштований.
        return TelegramClient(self.cfg.bot_token, str(chat_id), "", self.cfg.request_timeout)

    def _throttled(self, chat_id: int, command: str) -> bool:
        key = (chat_id, command)
        now = time.monotonic()
        last = self._last_handled.get(key, 0.0)
        if now - last < COOLDOWN_SECONDS:
            return True
        self._last_handled[key] = now
        return False

    # ------------------------------------------------------------ handling

    def handle_command(self, chat_id: int, text: str, addressed: bool) -> None:
        raw = text.strip().split()[0]
        name = raw.lstrip("/").split("@", 1)[0].casefold()

        if name in {"help", "start", "допомога", "команди", "commands"}:
            self._client_for(chat_id).send_message(catalog.help_text())
            return

        topic = catalog.resolve(raw)
        if topic is None:
            # У групі мовчимо на чужі команди — інакше бот засмічує чат.
            if addressed:
                self._client_for(chat_id).send_message(
                    f"Не знаю команди <code>{raw[:32]}</code>. /help — список."
                )
            return

        if self._throttled(chat_id, topic.key):
            log.info("throttle: %s у чаті %s", topic.key, chat_id)
            return

        telegram = self._client_for(chat_id)

        # Ціни різняться по магазинах, тож шукаємо в кожному.
        pairs = []
        for branch in self.cfg.branches:
            try:
                found = self.lookup.top(branch.branch_id, topic, limit=1)
            except Exception:
                log.exception("Не вдалося дістати ціни для '%s' у %s", topic.key, branch.label)
                continue
            if found:
                pairs.append((branch, found[0]))

        if not pairs:
            where = " і ".join(b.label for b in self.cfg.branches)
            telegram.send_message(
                f"🤷 Зараз немає знижок у категорії «{topic.title}» ({where})."
            )
            return

        for index, (top, labels) in enumerate(group_identical(pairs)):
            product = top.product
            label = branches_label(labels)
            caption = formatting.caption(product, label, "command", top.verdict, top.points)
            caption = f"👑 <b>{topic.title} — найвигідніше</b>\n\n{caption}"
            if index:
                time.sleep(self.cfg.message_delay)
            try:
                if product.image_url:
                    telegram.send_photo(product.image_url, caption, product.url)
                else:
                    telegram.send_message(formatting.fallback_text(
                        product, label, "command", top.verdict, top.points))
            except TelegramError as exc:
                log.warning("Фото не пройшло (%s) — шлю текстом", exc)
                telegram.send_message(formatting.fallback_text(
                    product, label, "command", top.verdict, top.points))

    # ------------------------------------------------------------ polling

    def run(self) -> None:
        telegram = TelegramClient(self.cfg.bot_token, "", "", self.cfg.request_timeout)
        offset = 0

        # Скидаємо накопичену чергу, щоб після рестарту не відповідати
        # на команди тижневої давнини.
        try:
            pending = telegram.get_updates()
            if pending:
                offset = pending[-1]["update_id"] + 1
                log.info("Пропускаю %d старих апдейтів", len(pending))
        except Exception:
            log.exception("Не вдалось прочитати стару чергу апдейтів")

        log.info("Слухаю команди: %s", ", ".join(f"/{t.key}" for t in catalog.TOPICS))

        while not self.stop.is_set():
            try:
                updates = telegram.get_updates(offset=offset, timeout=25)
            except Exception:
                log.exception("getUpdates впав — пауза 10с")
                self.stop.wait(10)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or update.get("channel_post")
                if not message:
                    continue
                text = (message.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                chat = message.get("chat", {})
                chat_id = chat.get("id")
                if chat_id is None:
                    continue
                addressed = (
                    chat.get("type") == "private"
                    or f"@{telegram_username(telegram)}" in text
                )
                log.info("Команда '%s' з чату %s", text.split()[0], chat_id)
                try:
                    self.handle_command(chat_id, text, addressed)
                except Exception:
                    log.exception("Обробка команди '%s' впала", text[:32])


_username_cache: dict[str, str] = {}


def telegram_username(client: TelegramClient) -> str:
    if "u" not in _username_cache:
        try:
            _username_cache["u"] = client.get_me().get("username", "")
        except Exception:
            _username_cache["u"] = ""
    return _username_cache["u"]


def start_in_background(cfg: Config, stop_event: threading.Event) -> threading.Thread:
    bot = CommandBot(cfg, stop_event)
    thread = threading.Thread(target=bot.run, name="command-bot", daemon=True)
    thread.start()
    return thread
