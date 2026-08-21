"""silpo-alco-watch — моніторинг акцій на алкоголь у Сільпо → Telegram."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta

import threading

from . import bot as bot_module
from . import web as web_module
from . import catalog, formatting, history
from .candidate import Candidate, branches_label, build_candidate, group_identical
from .config import CATEGORY_GROUPS, Config
from .silpo import Product, SilpoClient
from .state import State
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("silpo-alco-watch")

_stop = False


def _handle_signal(signum, _frame) -> None:
    global _stop
    log.info("Отримано сигнал %s — завершую після поточної ітерації", signum)
    _stop = True


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# --------------------------------------------------------------------- збір


def scan_catalog(cfg: Config, silpo: SilpoClient, state: State) -> dict[str, list[Candidate]]:
    """Обходить ВЕСЬ алкогольний каталог, пише історію і збирає кандидатів.

    Акційна плашка більше не фільтр: товар міг тихо подешевшати без жодної
    плашки, і навпаки — плашка часто висить на накрученій ціні.
    """
    result: dict[str, list[Candidate]] = {}

    for branch in cfg.branches:
        products: dict[str, Product] = {}
        statuses: dict[str, tuple[str, float | None]] = {}

        for group_key in cfg.groups:
            root_slug, group_title = CATEGORY_GROUPS[group_key]
            leaves = silpo.leaf_categories(branch.branch_id, cfg.delivery_type, root_slug)
            log.info("[%s] %s: %d листових категорій", branch.label, group_title, len(leaves))
            for leaf in leaves:
                slug = leaf.get("slug") or ""
                title = leaf.get("title") or group_title
                for raw in silpo.products(
                    branch.branch_id, cfg.delivery_type, slug, promo_only=False
                ):
                    product = silpo.to_product(raw, slug, title, group_key)
                    if not product.product_id or product.price <= 0:
                        continue
                    if product.product_id in products:
                        continue
                    products[product.product_id] = product
                    state.upsert_product(branch.branch_id, product)
                    statuses[product.product_id] = history.record(
                        state.conn,
                        branch.branch_id,
                        product.product_id,
                        product.price,
                        product.old_price,
                        bool(product.promo_labels) and product.old_price > product.price,
                    )
        state.commit()

        info = history.stats(state.conn, branch.branch_id)
        log.info(
            "[%s] каталог: %d позицій; в архіві %d товарів / %d записів про ціну",
            branch.label, len(products), info["products"], info["rows"],
        )

        candidates: list[Candidate] = []
        for product in products.values():
            if cfg.only_in_stock and product.stock <= 0:
                continue
            if cfg.exclude_online_only and product.online_only:
                continue
            status, _ = statuses.get(product.product_id, ("new", None))
            candidate = build_candidate(
                state.conn, branch.branch_id, product, cfg.history_points, status
            )
            state.save_verdict(branch.branch_id, product.product_id, candidate)
            if candidate.score < max(cfg.min_discount, 0.5):
                continue
            candidates.append(candidate)

        state.commit()
        candidates.sort(key=lambda c: c.sort_key)
        result[branch.branch_id] = candidates

        dropped = sum(1 for c in candidates if c.day_drop > 0)
        log.info(
            "[%s] кандидатів: %d (подешевшали з минулого разу: %d)",
            branch.label, len(candidates), dropped,
        )
    return result


# ------------------------------------------------------------------ прохід


def _pick_leader(cfg: Config, state: State, branch, candidates: list[Candidate]):
    """Чи є що сказати по цьому магазину. Повертає (кандидат, причина) або None."""
    if not candidates:
        log.info("[%s] кандидатів немає", branch.label)
        return None

    top = candidates[0]
    leader = top.product
    previous = json.loads(state.get_meta(f"top1:{branch.branch_id}", "{}") or "{}")

    if previous.get("product_id") == leader.product_id:
        if abs(float(previous.get("price", -1)) - leader.price) <= 0.01:
            log.info(
                "[%s] лідер не змінився: %s (−%.0f%%) — мовчу",
                branch.label, leader.title, top.score,
            )
            return None
        if not cfg.notify_on_price_drop:
            _remember_leader(state, branch, top)
            return None
        reason = "top1_cheaper"
    else:
        reason = "top1"

    log.info(
        "[%s] новий лідер: %s — %.0f ₴ (реальна знижка %.0f%%, за добу %.0f%%)",
        branch.label, leader.title, leader.price, top.score, top.day_drop,
    )
    return top, reason


def _remember_leader(state: State, branch, top: Candidate) -> None:
    state.set_meta(
        f"top1:{branch.branch_id}",
        json.dumps(
            {
                "product_id": top.product.product_id,
                "price": top.product.price,
                "title": top.product.title,
            },
            ensure_ascii=False,
        ),
    )


def _send_leaders(cfg: Config, state: State, telegram: TelegramClient, picks: list) -> int:
    """picks: [(branch, candidate, reason)]. Однакові лідери зливаються в одне."""
    if not picks:
        return 0

    sent = 0
    groups = group_identical([(branch, top) for branch, top, _ in picks])
    reason_by_product = {top.product.product_id: reason for _, top, reason in picks}

    for index, (top, labels) in enumerate(groups):
        product = top.product
        label = branches_label(labels)
        reason = reason_by_product.get(product.product_id, "top1")
        caption = formatting.caption(product, label, reason, top.verdict, top.points)

        if cfg.dry_run:
            print(caption)
            print("🖼", product.image_url or "(без фото)")
            print("🔗", product.url)
            continue

        if index:
            time.sleep(cfg.message_delay)
        try:
            if product.image_url:
                telegram.send_photo(product.image_url, caption, product.url)
            else:
                telegram.send_message(
                    formatting.fallback_text(product, label, reason, top.verdict, top.points))
        except TelegramError as exc:
            log.warning("Фото не пройшло (%s) — шлю текстом", exc)
            telegram.send_message(
                formatting.fallback_text(product, label, reason, top.verdict, top.points))
        sent += 1

    # Лідера памʼятаємо тільки після успішної відправки, щоб збій не «зʼїв»
    # повідомлення до наступної зміни лідера.
    if not cfg.dry_run:
        for branch, top, _ in picks:
            _remember_leader(state, branch, top)
    return sent


def run_once(cfg: Config) -> int:
    """Один прохід. Повертає кількість надісланих повідомлень."""
    silpo = SilpoClient(timeout=cfg.request_timeout, image_size=cfg.image_size)
    # DRY_RUN не повинен нічого запамʼятовувати — інакше перший реальний
    # запуск вирішить, що все вже відпрацьовано.
    state = State(":memory:" if cfg.dry_run else cfg.state_path)
    telegram = TelegramClient(cfg.bot_token, cfg.chat_id, cfg.thread_id, cfg.request_timeout)

    silent = cfg.notify_mode == "off"
    top1_mode = cfg.notify_mode == "top1"
    seeding = (not silent) and (not top1_mode) and cfg.seed_on_first_run and state.is_empty
    if seeding:
        log.info("Перший запуск: наповнюю базу без сповіщень (SEED_ON_FIRST_RUN=true)")

    sent = 0
    try:
        found = scan_catalog(cfg, silpo, state)

        if silent:
            # Автопости вимкнені: щоденний обхід потрібен лише для того, щоб
            # накопичувалась історія цін — вона живить відповіді на команди.
            for branch in cfg.branches:
                best = found.get(branch.branch_id, [])
                if best:
                    top = best[0]
                    log.info(
                        "[%s] найвигідніше зараз: %s — %.0f ₴ (%.0f%%). Не надсилаю: NOTIFY_MODE=off",
                        branch.label, top.product.title, top.product.price, top.score,
                    )
            return 0

        if top1_mode:
            picks = []
            for branch in cfg.branches:
                chosen = _pick_leader(cfg, state, branch, found.get(branch.branch_id, []))
                if chosen:
                    picks.append((branch, chosen[0], chosen[1]))
            sent += _send_leaders(cfg, state, telegram, picks)
            log.info("Готово. Надіслано повідомлень: %d", sent)
            return sent

        for branch in cfg.branches:
            candidates = found.get(branch.branch_id, [])
            to_notify: list[tuple[Candidate, str]] = []

            for candidate in candidates:
                product = candidate.product
                reason, _ = state.classify(branch.branch_id, product.product_id, product.price)
                if reason == "cheaper" and not cfg.notify_on_price_drop:
                    reason = "known"
                if reason in {"new", "cheaper"} and not seeding:
                    to_notify.append((candidate, reason))
                else:
                    state.record(
                        branch.branch_id, product.product_id, product.title,
                        product.price, product.old_price, notified=seeding,
                    )
            state.commit()

            if seeding:
                log.info("[%s] у базу записано %d позицій", branch.label, len(candidates))
                continue
            if not to_notify:
                log.info("[%s] нічого нового", branch.label)
                continue

            capped = to_notify[: cfg.max_messages_per_run]
            extra = len(to_notify) - len(capped)
            if extra:
                log.info(
                    "[%s] %d нових, надсилаю топ-%d (ліміт MAX_MESSAGES_PER_RUN)",
                    branch.label, len(to_notify), len(capped),
                )

            if cfg.dry_run:
                print(formatting.run_header(len(to_notify), branch.label, extra))
                for candidate, reason in capped:
                    print("-" * 60)
                    print(formatting.caption(
                        candidate.product, branch.label, reason,
                        candidate.verdict, candidate.points))
                continue

            telegram.send_message(formatting.run_header(len(to_notify), branch.label, extra))
            sent += 1
            time.sleep(cfg.message_delay)

            for candidate, reason in capped:
                product = candidate.product
                caption = formatting.caption(
                    product, branch.label, reason, candidate.verdict, candidate.points)
                try:
                    if product.image_url:
                        telegram.send_photo(product.image_url, caption, product.url)
                    else:
                        telegram.send_message(formatting.fallback_text(
                            product, branch.label, reason, candidate.verdict, candidate.points))
                except TelegramError as exc:
                    log.warning("Фото не пройшло для %s (%s) — шлю текстом", product.slug, exc)
                    try:
                        telegram.send_message(formatting.fallback_text(
                            product, branch.label, reason, candidate.verdict, candidate.points))
                    except TelegramError as exc2:
                        log.error("Не вдалося надіслати %s: %s", product.slug, exc2)
                        continue
                sent += 1
                state.record(
                    branch.branch_id, product.product_id, product.title,
                    product.price, product.old_price, notified=True,
                )
                state.commit()
                time.sleep(cfg.message_delay)

        if seeding:
            state.set_meta("seeded_at", datetime.now(cfg.tz).isoformat(timespec="seconds"))
    finally:
        state.close()

    log.info("Готово. Надіслано повідомлень: %d", sent)
    return sent


# ------------------------------------------------------------ планувальник


def next_run_at(cfg: Config, now: datetime) -> datetime:
    candidates: list[datetime] = []
    for slot in cfg.schedule_times:
        hh, _, mm = slot.partition(":")
        today = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        candidates.append(today if today > now else today + timedelta(days=1))
    return min(candidates)


def watch(cfg: Config) -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    stop_event = threading.Event()
    if cfg.enable_commands:
        bot_module.start_in_background(cfg, stop_event)
    else:
        log.info("Слухач команд вимкнено (ENABLE_COMMANDS=false)")

    if cfg.web_enabled:
        web_module.start_in_background(cfg, stop_event)
    else:
        log.info("Дашборд вимкнено (WEB_ENABLED=false)")

    if os.getenv("RUN_ON_START", "true").lower() in {"1", "true", "yes"}:
        try:
            run_once(cfg)
        except Exception:
            log.exception("Прохід на старті впав")

    while not _stop:
        now = datetime.now(cfg.tz)
        target = next_run_at(cfg, now)
        wait_seconds = (target - now).total_seconds()
        log.info("Наступна перевірка: %s (через %.1f год)", target.strftime("%Y-%m-%d %H:%M %Z"), wait_seconds / 3600)

        # Спимо короткими інтервалами, щоб швидко реагувати на SIGTERM.
        while wait_seconds > 0 and not _stop:
            chunk = min(30.0, wait_seconds)
            time.sleep(chunk)
            wait_seconds -= chunk

        if _stop:
            break
        try:
            run_once(cfg)
        except Exception:
            log.exception("Прохід за розкладом впав — чекаю наступного")

    stop_event.set()
    log.info("Зупинено")


# -------------------------------------------------------------------- CLI


def cmd_branches(args: argparse.Namespace) -> int:
    silpo = SilpoClient()
    matches = silpo.search_branches(args.search or "")
    if not matches:
        print(f"Нічого не знайдено за запитом: {args.search!r}")
        return 1
    for b in matches[: args.limit]:
        status = "" if b.get("open") else "  (зачинено)"
        print(f"{b['branchId']}  {b.get('cityFull','')}, {b.get('addressFull','')}{status}")
    print(f"\nЗнайдено: {len(matches)} (показано {min(len(matches), args.limit)})")
    print("Скопіюй потрібний рядок у SILPO_BRANCHES як '<branchId>:Сільпо, <адреса>'")
    return 0


def cmd_chatid(_args: argparse.Namespace) -> int:
    cfg = Config.from_env()
    if not cfg.bot_token:
        print("TELEGRAM_BOT_TOKEN не заданий")
        return 1
    telegram = TelegramClient(cfg.bot_token, "")
    me = telegram.get_me()
    print(f"Бот: {me.get('first_name')} @{me.get('username')} (id {me.get('id')})")
    updates = telegram.get_updates()
    chats: dict[int, tuple[str, str]] = {}
    for update in updates:
        for key in ("message", "channel_post", "edited_message", "my_chat_member"):
            payload = update.get(key)
            if payload and "chat" in payload:
                chat = payload["chat"]
                chats[chat["id"]] = (chat.get("type", "?"), chat.get("title") or chat.get("username") or "")
    if not chats:
        print(
            "\nЧатів не видно. Додай бота в групу і надішли там будь-яку команду,\n"
            "напр. /start@<username бота> — після цього запусти цю команду ще раз."
        )
        return 1
    print("\nЗнайдені чати:")
    for chat_id, (chat_type, title) in chats.items():
        print(f"  TELEGRAM_CHAT_ID={chat_id}   ({chat_type}) {title}")
    return 0


def cmd_test(_args: argparse.Namespace) -> int:
    cfg = Config.from_env()
    telegram = TelegramClient(cfg.bot_token, cfg.chat_id, cfg.thread_id)
    telegram.send_message(
        "✅ <b>silpo-alco-watch</b> на звʼязку.\n"
        f"Філій: {len(cfg.branches)} · категорій: {', '.join(cfg.groups)}\n"
        f"Розклад: {', '.join(cfg.schedule_times)} ({cfg.tz})"
    )
    print("Тестове повідомлення надіслано.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="silpo-alco-watch", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("watch", help="постійний режим за розкладом (за замовчуванням)")
    sub.add_parser("run", help="один прохід і вихід")
    sub.add_parser("test", help="надіслати тестове повідомлення в чат")
    sub.add_parser("chatid", help="показати chat_id, які бачить бот")
    sub.add_parser("config", help="показати поточну конфігурацію")
    sub.add_parser("bot", help="лише слухач команд, без розкладу")
    sub.add_parser("setcommands", help="зареєструвати меню команд у Telegram")
    sub.add_parser("history", help="статистика зібраної історії цін")
    sub.add_parser("web", help="лише дашборд, без розкладу і бота")

    p_branches = sub.add_parser("branches", help="знайти branchId за містом/адресою")
    p_branches.add_argument("--search", default="", help="місто або частина адреси")
    p_branches.add_argument("--limit", type=int, default=40)

    args = parser.parse_args(argv)
    command = args.command or "watch"

    if command == "branches":
        setup_logging("WARNING")
        return cmd_branches(args)
    if command == "chatid":
        setup_logging("WARNING")
        return cmd_chatid(args)

    cfg = Config.from_env()
    setup_logging(cfg.log_level)

    if cfg.unknown_groups:
        log.warning("Невідомі групи категорій пропущено: %s", ", ".join(cfg.unknown_groups))

    if command == "config":
        print(json.dumps(
            {
                "branches": [f"{b.branch_id}:{b.label}" for b in cfg.branches],
                "groups": cfg.groups,
                "delivery_type": cfg.delivery_type,
                "min_discount": cfg.min_discount,
                "only_in_stock": cfg.only_in_stock,
                "exclude_online_only": cfg.exclude_online_only,
                "schedule": cfg.schedule_times,
                "tz": str(cfg.tz),
                "max_messages_per_run": cfg.max_messages_per_run,
                "state_path": cfg.state_path,
                "chat_id": cfg.chat_id or "(не задано)",
                "token": "задано" if cfg.bot_token else "(не задано)",
            },
            ensure_ascii=False, indent=2,
        ))
        return 0

    problems = cfg.validate()
    if command == "test" and problems:
        problems = [p for p in problems if "SILPO_" not in p]
    if problems:
        for problem in problems:
            log.error("Конфігурація: %s", problem)
        return 2

    if command == "test":
        return cmd_test(args)
    if command == "setcommands":
        telegram = TelegramClient(cfg.bot_token, cfg.chat_id)
        telegram.set_my_commands(list(catalog.MENU_COMMANDS))
        print(f"Зареєстровано команд: {len(catalog.MENU_COMMANDS)}")
        for name, description in catalog.MENU_COMMANDS:
            print(f"  /{name} — {description}")
        return 0
    if command == "history":
        state = State(cfg.state_path)
        for branch in cfg.branches:
            info = history.stats(state.conn, branch.branch_id)
            print(f"{branch.label}")
            print(f"  товарів в архіві: {info['products']}")
            print(f"  записів про зміну ціни: {info['rows']}")
            print(f"  збираємо з: {info['since'] or '—'}")
        state.close()
        return 0
    if command == "web":
        stop_event = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: stop_event.set())
        web_module.serve(cfg, stop_event)
        return 0
    if command == "bot":
        stop_event = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: stop_event.set())
        bot_module.CommandBot(cfg, stop_event).run()
        return 0
    if command == "run":
        run_once(cfg)
        return 0
    watch(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
