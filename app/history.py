"""Історія цін: власний архів, бо API його не віддає.

Сільпо не має ендпоінта з історією — доступна лише поточна ціна, перекреслена
`oldPrice` і вікно акції. Тому щоб відрізнити справжню знижку від накрученої,
ми щодня знімаємо ціни всього каталогу і рахуємо базову ціну самі.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    branch_id    TEXT NOT NULL,
    product_id   TEXT NOT NULL,
    price        REAL NOT NULL,
    old_price    REAL,
    on_promo     INTEGER NOT NULL,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    PRIMARY KEY (branch_id, product_id, first_seen)
);
CREATE INDEX IF NOT EXISTS idx_history_product
    ON price_history(branch_id, product_id, last_seen);
"""

# Скільки днів історії враховувати при обчисленні звичайної ціни.
DEFAULT_WINDOW_DAYS = 90
# Коротший за це період «звичайної» ціни вважаємо накруткою перед акцією.
SPIKE_MAX_DAYS = 10


@dataclass(frozen=True)
class PriceVerdict:
    """Що ми насправді знаємо про знижку."""

    claimed_discount: float          # відсоток, який малює Сільпо
    regular_price: float | None      # звичайна ціна за нашими спостереженнями
    honest_discount: float | None    # відсоток відносно звичайної ціни
    observed_days: int               # скільки днів історії маємо
    inflated: bool                   # перекреслену ціну підняли перед акцією
    confident: bool                  # історії достатньо, щоб робити висновок

    @property
    def effective_discount(self) -> float:
        """Знижка, якій можна вірити: чесна, якщо знаємо, інакше заявлена."""
        if self.confident and self.honest_discount is not None:
            return self.honest_discount
        return self.claimed_discount


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def init(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def record(conn, branch_id: str, product_id: str, price: float,
           old_price: float, on_promo: bool) -> tuple[str, float | None]:
    """Повертає ('new'|'same'|'changed', попередня_ціна)."""
    """Пише лише зміни ціни, а не щоденні знімки.

    1642 товари × 2 рази на добу дало б мільйон рядків на рік ні за що;
    так виходить кілька десятків тисяч.
    """
    now = _now()
    row = conn.execute(
        "SELECT price, on_promo, first_seen FROM price_history "
        "WHERE branch_id = ? AND product_id = ? "
        "ORDER BY last_seen DESC LIMIT 1",
        (branch_id, product_id),
    ).fetchone()

    if row is not None and abs(row["price"] - price) < 0.01 and row["on_promo"] == int(on_promo):
        conn.execute(
            "UPDATE price_history SET last_seen = ?, old_price = ? "
            "WHERE branch_id = ? AND product_id = ? AND first_seen = ?",
            (now, old_price, branch_id, product_id, row["first_seen"]),
        )
        return "same", float(row["price"])

    conn.execute(
        "INSERT OR REPLACE INTO price_history"
        "(branch_id, product_id, price, old_price, on_promo, first_seen, last_seen) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (branch_id, product_id, price, old_price, int(on_promo), now, now),
    )
    if row is None:
        return "new", None
    return "changed", float(row["price"])


def _weighted_median(samples: list[tuple[float, float]]) -> float | None:
    """Медіана цін, зважена на кількість днів, що ціна протрималась.

    Саме зважування ламає схему «підняли на 3 дні — і знизили»: короткий
    сплеск не перебиває ціну, яка стояла місяць.
    """
    if not samples:
        return None
    ordered = sorted(samples, key=lambda s: s[0])
    total = sum(weight for _, weight in ordered)
    if total <= 0:
        return ordered[len(ordered) // 2][0]
    half, running = total / 2, 0.0
    for price, weight in ordered:
        running += weight
        if running >= half:
            return price
    return ordered[-1][0]


def verdict(conn, branch_id: str, product_id: str, price: float,
            old_price: float, window_days: int = DEFAULT_WINDOW_DAYS) -> PriceVerdict:
    claimed = ((old_price - price) / old_price * 100.0) if old_price > price > 0 else 0.0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat(timespec="seconds")

    rows = conn.execute(
        "SELECT price, on_promo, first_seen, last_seen FROM price_history "
        "WHERE branch_id = ? AND product_id = ? AND last_seen >= ? "
        "ORDER BY first_seen",
        (branch_id, product_id, cutoff),
    ).fetchall()

    if not rows:
        return PriceVerdict(claimed, None, None, 0, False, False)

    span_start = min(_parse(r["first_seen"]) for r in rows)
    observed_days = max(0, (datetime.now(timezone.utc) - span_start).days)

    # Базу рахуємо лише по періодах поза акцією — це і є "звичайна" ціна.
    samples: list[tuple[float, float]] = []
    for row in rows:
        if row["on_promo"]:
            continue
        held_days = max(
            0.5, (_parse(row["last_seen"]) - _parse(row["first_seen"])).total_seconds() / 86400
        )
        samples.append((row["price"], held_days))

    regular = _weighted_median(samples)
    if regular is None or regular <= 0:
        return PriceVerdict(claimed, None, None, observed_days, False, False)

    honest = (regular - price) / regular * 100.0 if regular > price else 0.0

    # Перекреслена ціна суттєво вища за все, що ми бачили на полиці —
    # або трималась лише кілька днів перед акцією.
    spike = False
    for row in rows:
        if row["on_promo"] or row["price"] <= regular * 1.05:
            continue
        held = (_parse(row["last_seen"]) - _parse(row["first_seen"])).total_seconds() / 86400
        if held <= SPIKE_MAX_DAYS:
            spike = True
    inflated = spike or old_price > regular * 1.10

    # Один-два дні спостережень — ще не історія, висновків не робимо.
    confident = observed_days >= 7 and bool(samples)
    return PriceVerdict(claimed, regular, honest, observed_days, inflated, confident)


def stats(conn, branch_id: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS rows, COUNT(DISTINCT product_id) AS products, "
        "MIN(first_seen) AS since FROM price_history WHERE branch_id = ?",
        (branch_id,),
    ).fetchone()
    return {"rows": row["rows"], "products": row["products"], "since": row["since"]}


@dataclass(frozen=True)
class PricePoint:
    when: datetime
    price: float
    on_promo: bool


def recent_points(conn, branch_id: str, product_id: str, limit: int = 4) -> list[PricePoint]:
    """Останні різні ціни товару, від давніших до свіжіших."""
    rows = conn.execute(
        "SELECT price, on_promo, first_seen FROM price_history "
        "WHERE branch_id = ? AND product_id = ? "
        "ORDER BY first_seen DESC LIMIT ?",
        (branch_id, product_id, limit * 3),
    ).fetchall()

    # Схлопуємо сусідні записи з однаковою ціною: вони зʼявляються, коли
    # змінився лише промо-прапорець, а в динаміці читались би як "299 → 299".
    points: list[PricePoint] = []
    for row in reversed(rows):
        price = float(row["price"])
        if points and abs(points[-1].price - price) < 0.01:
            # лишаємо найранішу появу ціни, але підхоплюємо промо-мітку
            if row["on_promo"] and not points[-1].on_promo:
                points[-1] = PricePoint(points[-1].when, price, True)
            continue
        points.append(PricePoint(_parse(row["first_seen"]), price, bool(row["on_promo"])))
    return points[-limit:]


def previous_price(conn, branch_id: str, product_id: str) -> float | None:
    """Ціна, що була до поточної. None, якщо товар бачимо вперше."""
    rows = conn.execute(
        "SELECT price FROM price_history WHERE branch_id = ? AND product_id = ? "
        "ORDER BY first_seen DESC LIMIT 2",
        (branch_id, product_id),
    ).fetchall()
    return float(rows[1]["price"]) if len(rows) > 1 else None
