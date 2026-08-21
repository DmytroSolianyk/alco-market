"""Конфігурація зі змінних оточення."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

# Корені груп категорій у дереві "Алкоголь" (alkogol-22).
# Продукти віддаються тільки для листових категорій, тому з кожного кореня
# розкручується все піддерево — див. SilpoClient.leaf_slugs().
CATEGORY_GROUPS: dict[str, tuple[str, str]] = {
    "strong": ("mitsnyi-alkogol-4458", "Міцний алкоголь"),
    "wine_still": ("tykhi-vyna-4459", "Тихі вина"),
    "wine_sparkling": ("igrysti-vyna-ta-shampanske-4460", "Ігристі вина"),
    "vermouth": ("vermuty-4461", "Вермути"),
    "beer": ("pyvo-4503", "Пиво"),
    "cider": ("slaboalkogolni-napoi-sydr-4463", "Слабоалкогольні, сидр"),
    "nonalco": ("bezalkogolnyi-alkogol-4464", "Безалкогольний алкоголь"),
}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    try:
        return float(raw.replace(",", ".")) if raw else default
    except ValueError:
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = _env(name)
    if not raw:
        return list(default)
    return [p.strip() for p in raw.split(",") if p.strip()]


BRANCH_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


@dataclass(frozen=True)
class Branch:
    """Одна філія Сільпо, за якою стежимо."""

    branch_id: str
    label: str

    @staticmethod
    def parse(raw: str) -> "Branch":
        # Формат: "<branchId>:<людська назва>"; назва необовʼязкова.
        # Назва майже завжди містить кому ("Сільпо, вул. X"), тому філії
        # у SILPO_BRANCHES розділяються крапкою з комою, а не комою.
        branch_id, _, label = raw.partition(":")
        branch_id = branch_id.strip()
        label = label.strip() or "Сільпо"
        return Branch(branch_id=branch_id, label=label)

    @property
    def looks_valid(self) -> bool:
        return bool(BRANCH_ID_RE.fullmatch(self.branch_id))


@dataclass(frozen=True)
class Config:
    bot_token: str
    chat_id: str
    branches: list[Branch]
    groups: list[str]
    delivery_type: str
    min_discount: float
    only_in_stock: bool
    exclude_online_only: bool
    schedule_times: list[str]
    tz: ZoneInfo
    state_path: str
    max_messages_per_run: int
    seed_on_first_run: bool
    notify_mode: str
    enable_commands: bool
    track_history: bool
    history_points: int
    rank_by_honest: bool
    notify_on_price_drop: bool
    message_delay: float
    dry_run: bool
    request_timeout: int
    log_level: str
    thread_id: str = ""
    image_size: str = "500x500"
    unknown_groups: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Config":
        raw_groups = _env_list("SILPO_CATEGORY_GROUPS", ["strong", "beer", "cider", "vermouth"])
        groups = [g for g in raw_groups if g in CATEGORY_GROUPS]
        unknown = [g for g in raw_groups if g not in CATEGORY_GROUPS]

        branches_raw = [
            part.strip() for part in _env("SILPO_BRANCHES").split(";") if part.strip()
        ]
        branches = [Branch.parse(b) for b in branches_raw]

        return cls(
            bot_token=_env("TELEGRAM_BOT_TOKEN"),
            chat_id=_env("TELEGRAM_CHAT_ID"),
            thread_id=_env("TELEGRAM_THREAD_ID"),
            branches=branches,
            groups=groups,
            unknown_groups=unknown,
            delivery_type=_env("SILPO_DELIVERY_TYPE", "SelfPickup"),
            min_discount=_env_float("MIN_DISCOUNT_PERCENT", 0.0),
            only_in_stock=_env_bool("ONLY_IN_STOCK", True),
            exclude_online_only=_env_bool("EXCLUDE_ONLINE_ONLY", False),
            schedule_times=_env_list("SCHEDULE_TIMES", ["09:00"]),
            tz=ZoneInfo(_env("TZ", "Europe/Kyiv")),
            state_path=_env("STATE_PATH", "/data/state.db"),
            max_messages_per_run=_env_int("MAX_MESSAGES_PER_RUN", 25),
            seed_on_first_run=_env_bool("SEED_ON_FIRST_RUN", True),
            notify_mode=_env("NOTIFY_MODE", "off").lower(),
            enable_commands=_env_bool("ENABLE_COMMANDS", True),
            track_history=_env_bool("TRACK_PRICE_HISTORY", True),
            history_points=_env_int("HISTORY_POINTS", 4),
            rank_by_honest=_env_bool("RANK_BY_HONEST_DISCOUNT", True),
            notify_on_price_drop=_env_bool("NOTIFY_ON_PRICE_DROP", True),
            message_delay=_env_float("MESSAGE_DELAY_SECONDS", 3.5),
            dry_run=_env_bool("DRY_RUN", False),
            request_timeout=_env_int("REQUEST_TIMEOUT", 30),
            log_level=_env("LOG_LEVEL", "INFO").upper(),
        )

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.bot_token:
            problems.append("TELEGRAM_BOT_TOKEN не заданий")
        if not self.chat_id and not self.dry_run:
            problems.append("TELEGRAM_CHAT_ID не заданий")
        if not self.branches:
            problems.append(
                "SILPO_BRANCHES не заданий (формат: <branchId>:<назва>, кілька філій — через ';')"
            )
        for branch in self.branches:
            if not branch.looks_valid:
                problems.append(
                    f"SILPO_BRANCHES: '{branch.branch_id}' не схоже на branchId (очікується UUID). "
                    "Кілька філій розділяються ';', бо назва містить кому."
                )
        if not self.groups:
            problems.append(
                "SILPO_CATEGORY_GROUPS порожній або невалідний; доступні: "
                + ", ".join(CATEGORY_GROUPS)
            )
        if self.notify_mode not in {"off", "top1", "all"}:
            problems.append(
                f"NOTIFY_MODE: '{self.notify_mode}' — очікується 'off', 'top1' або 'all'"
            )
        for t in self.schedule_times:
            hh, _, mm = t.partition(":")
            if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                problems.append(f"SCHEDULE_TIMES: '{t}' не у форматі HH:MM")
        return problems
