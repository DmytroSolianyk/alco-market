"""Команди бота → категорії Сільпо, з кешем відповідей."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .candidate import Candidate, build_candidate
from .config import CATEGORY_GROUPS, Config
from .silpo import SilpoClient

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600


@dataclass(frozen=True)
class Topic:
    """Те, що бот шукає у відповідь на одну команду."""

    key: str          # канонічна назва для логів і меню
    title: str        # як показуємо людині
    root_slug: str    # корінь у дереві категорій ("" = усі відстежувані групи)


# Канонічні теми. root_slug може бути і листом, і проміжною категорією —
# leaf_categories() розкрутить її до листів у будь-якому разі.
TOPICS: tuple[Topic, ...] = (
    Topic("топ", "Уся акційна поличка", ""),
    Topic("міцний", "Міцний алкоголь", "mitsnyi-alkogol-4458"),
    Topic("горілка", "Горілка", "gorilka-4472"),
    Topic("віскі", "Віскі", "viski-4466"),
    Topic("коньяк", "Коньяк і бренді", "koniak-brendi-4467"),
    Topic("ром", "Ром", "rom-4468"),
    Topic("джин", "Джин", "dzhyn-4469"),
    Topic("текіла", "Текіла", "tekila-agavovi-dystyliaty-4470"),
    Topic("лікер", "Лікери, бальзами, біттери", "likery-balzamy-bittery-4474"),
    Topic("настоянка", "Настоянки і наливки", "nastoianky-nalyvky-4473"),
    Topic("вермут", "Вермути", "vermuty-4461"),
    Topic("пиво", "Пиво", "pyvo-4503"),
    Topic("крафт", "Крафтове пиво", "kraftove-pyvo-4506"),
    Topic("сидр", "Сидр і збитень", "sydr-ta-zbyten-4480"),
    Topic("вино", "Тихі вина", "tykhi-vyna-4459"),
    Topic("ігристе", "Ігристі та шампанське", "igrysti-vyna-ta-shampanske-4460"),
)

# Синоніми → канонічна тема. Латиниця обовʼязкова: Telegram підсвічує
# як команду лише /ascii, і саме її можна покласти в меню бота.
ALIASES: dict[str, str] = {
    "топ": "топ", "top": "топ", "all": "топ", "усе": "топ", "все": "топ",
    "міцний": "міцний", "міцне": "міцний", "mitsne": "міцний", "strong": "міцний",
    "горілка": "горілка", "горiлка": "горілка", "vodka": "горілка", "gorilka": "горілка",
    "віскі": "віскі", "вiскi": "віскі", "виски": "віскі",
    "whisky": "віскі", "whiskey": "віскі", "viski": "віскі",
    "коньяк": "коньяк", "бренді": "коньяк", "brandy": "коньяк", "cognac": "коньяк",
    "ром": "ром", "rum": "ром", "rom": "ром",
    "джин": "джин", "gin": "джин", "dzhyn": "джин",
    "текіла": "текіла", "текила": "текіла", "tequila": "текіла", "tekila": "текіла",
    "лікер": "лікер", "лікери": "лікер", "ликер": "лікер",
    "liqueur": "лікер", "liker": "лікер", "bitter": "лікер",
    "настоянка": "настоянка", "настоянки": "настоянка", "наливка": "настоянка",
    "nastoianka": "настоянка",
    "вермут": "вермут", "вермути": "вермут", "vermouth": "вермут", "vermut": "вермут",
    "пиво": "пиво", "beer": "пиво", "pyvo": "пиво", "pivo": "пиво",
    "крафт": "крафт", "craft": "крафт", "ipa": "крафт",
    "сидр": "сидр", "cider": "сидр", "sydr": "сидр",
    "вино": "вино", "wine": "вино", "vyno": "вино", "vino": "вино",
    "ігристе": "ігристе", "игристое": "ігристе", "шампанське": "ігристе",
    "sparkling": "ігристе", "champagne": "ігристе", "igryste": "ігристе",
}

# Що показувати в меню команд Telegram (тільки латиниця — вимога Telegram).
MENU_COMMANDS: tuple[tuple[str, str], ...] = (
    ("top", "Топ-1 знижка серед усього"),
    ("vodka", "Топ-1 на горілку"),
    ("beer", "Топ-1 на пиво"),
    ("whisky", "Топ-1 на віскі"),
    ("brandy", "Топ-1 на коньяк і бренді"),
    ("rum", "Топ-1 на ром"),
    ("gin", "Топ-1 на джин"),
    ("tequila", "Топ-1 на текілу"),
    ("liqueur", "Топ-1 на лікери"),
    ("vermouth", "Топ-1 на вермут"),
    ("craft", "Топ-1 на крафтове пиво"),
    ("cider", "Топ-1 на сидр"),
    ("wine", "Топ-1 на тихі вина"),
    ("sparkling", "Топ-1 на ігристе"),
    ("help", "Список команд"),
)

TOPIC_BY_KEY = {t.key: t for t in TOPICS}


def resolve(command: str) -> Topic | None:
    """'/Пиво@bot' -> Topic('пиво')."""
    name = command.strip().lstrip("/").split("@", 1)[0].strip().casefold()
    key = ALIASES.get(name)
    return TOPIC_BY_KEY.get(key) if key else None


def help_text() -> str:
    lines = [
        "🍾 <b>Що я вмію</b>",
        "",
        "Надішли команду — відповім товаром з найбільшою знижкою в цій категорії:",
        "",
    ]
    for topic in TOPICS:
        latin = next(
            (cmd for cmd, _ in MENU_COMMANDS if ALIASES.get(cmd) == topic.key), None
        )
        cyr = f"/{topic.key}"
        both = f"{cyr}  або  /{latin}" if latin else cyr
        lines.append(f"{both} — {topic.title}")
    lines += [
        "",
        "<i>Латинські варіанти є в меню команд. Працюють обидва написання.</i>",
    ]
    return "\n".join(lines)


class PromoLookup:
    """Дістає найвигідніші позиції по темі. Кешує, щоб не бити по API щоразу."""

    def __init__(self, cfg: Config, silpo: SilpoClient, conn=None) -> None:
        self.cfg = cfg
        self.silpo = silpo
        self.conn = conn
        self._cache: dict[tuple[str, str], tuple[float, list[Candidate]]] = {}

    def _roots_for(self, topic: Topic) -> list[tuple[str, str]]:
        if topic.root_slug:
            return [(topic.root_slug, topic.title)]
        return [CATEGORY_GROUPS[g] for g in self.cfg.groups]

    def top(self, branch_id: str, topic: Topic, limit: int = 1) -> list[Candidate]:
        cache_key = (branch_id, topic.key)
        hit = self._cache.get(cache_key)
        if hit and (time.monotonic() - hit[0]) < CACHE_TTL_SECONDS:
            log.debug("кеш: %s", topic.key)
            return hit[1][:limit]

        found = {}
        for root_slug, root_title in self._roots_for(topic):
            for leaf in self.silpo.leaf_categories(
                branch_id, self.cfg.delivery_type, root_slug
            ):
                slug = leaf.get("slug") or ""
                title = leaf.get("title") or root_title
                # Скануємо весь асортимент, а не тільки акційні плашки:
                # реальне падіння ціни часто буває без жодної плашки.
                for raw in self.silpo.products(
                    branch_id, self.cfg.delivery_type, slug, promo_only=False
                ):
                    product = self.silpo.to_product(raw, slug, title, topic.key)
                    if not product.product_id or product.price <= 0:
                        continue
                    if self.cfg.only_in_stock and product.stock <= 0:
                        continue
                    if self.cfg.exclude_online_only and product.online_only:
                        continue
                    found.setdefault(product.product_id, product)

        candidates = [
            build_candidate(self.conn, branch_id, product, self.cfg.history_points)
            for product in found.values()
        ] if self.conn is not None else []

        if self.conn is None:
            # Без бази рахувати чесну знижку нічим — падаємо на плашку Сільпо.
            candidates = [
                Candidate(product=p, verdict=None, points=[], previous_price=None, status="same")
                for p in found.values()
            ]

        candidates = [c for c in candidates if c.score > 0]
        candidates.sort(key=lambda c: c.sort_key)
        self._cache[cache_key] = (time.monotonic(), candidates)
        log.info("тема '%s': %d позицій зі знижкою з %d", topic.key, len(candidates), len(found))
        return candidates[:limit]
