"""Стан у SQLite: що вже бачили і про що вже писали в Telegram."""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from . import history


def register_functions(conn) -> None:
    """SQLite LIKE регістронезалежний лише для ASCII, тож «віскі» не знайшов би
    «Віскі Kamiki». Даємо йому Python-ський casefold."""
    conn.create_function("ulower", 1, lambda s: s.casefold() if s else s, deterministic=True)

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    branch_id      TEXT NOT NULL,
    product_id     TEXT NOT NULL,
    title          TEXT,
    price          REAL,
    old_price      REAL,
    notified_price REAL,
    first_seen     TEXT,
    last_seen      TEXT,
    PRIMARY KEY (branch_id, product_id)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Довідник товарів: історія цін оперує лише product_id, а дашборду треба
-- назви, картинки, категорії та поточні залишки.
CREATE TABLE IF NOT EXISTS products (
    branch_id      TEXT NOT NULL,
    product_id     TEXT NOT NULL,
    title          TEXT,
    slug           TEXT,
    url            TEXT,
    image          TEXT,
    brand          TEXT,
    category_slug  TEXT,
    category_title TEXT,
    group_key      TEXT,
    display_ratio  TEXT,
    display_price  REAL,
    price          REAL,
    old_price      REAL,
    stock          REAL,
    online_only    INTEGER,
    rating         REAL,
    rating_count   INTEGER,
    first_seen     TEXT,
    last_seen      TEXT,
    PRIMARY KEY (branch_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_products_title ON products(title);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(branch_id, category_slug);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # check_same_thread=False: слухач команд читає ту саму базу з іншого
        # потоку; WAL прибирає взаємне блокування читача і планувальника.
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=15000")
        with closing(self.conn.cursor()) as cur:
            cur.executescript(SCHEMA)
        self.conn.commit()
        history.init(self.conn)
        register_functions(self.conn)

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ meta

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    @property
    def is_empty(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) AS n FROM seen").fetchone()["n"] == 0

    # ----------------------------------------------------------------- seen

    def classify(self, branch_id: str, product_id: str, price: float) -> tuple[str, float | None]:
        """Повертає ('new'|'cheaper'|'known', попередня_ціна_сповіщення)."""
        row = self.conn.execute(
            "SELECT notified_price FROM seen WHERE branch_id = ? AND product_id = ?",
            (branch_id, product_id),
        ).fetchone()
        if row is None:
            return "new", None
        previous = row["notified_price"]
        if previous is not None and price < previous - 0.01:
            return "cheaper", float(previous)
        return "known", (float(previous) if previous is not None else None)

    def record(
        self,
        branch_id: str,
        product_id: str,
        title: str,
        price: float,
        old_price: float,
        notified: bool,
    ) -> None:
        now = _now()
        self.conn.execute(
            """
            INSERT INTO seen(branch_id, product_id, title, price, old_price,
                             notified_price, first_seen, last_seen)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(branch_id, product_id) DO UPDATE SET
                title          = excluded.title,
                price          = excluded.price,
                old_price      = excluded.old_price,
                last_seen      = excluded.last_seen,
                notified_price = CASE WHEN ? THEN excluded.price ELSE seen.notified_price END
            """,
            (
                branch_id,
                product_id,
                title,
                price,
                old_price,
                price if notified else None,
                now,
                now,
                1 if notified else 0,
            ),
        )

    def upsert_product(self, branch_id: str, product) -> None:
        """Освіжає довідник товару. Ціни живуть окремо, тут — метадані."""
        now = _now()
        self.conn.execute(
            """
            INSERT INTO products(branch_id, product_id, title, slug, url, image, brand,
                                 category_slug, category_title, group_key, display_ratio,
                                 display_price, price, old_price, stock, online_only,
                                 rating, rating_count, first_seen, last_seen)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(branch_id, product_id) DO UPDATE SET
                title=excluded.title, slug=excluded.slug, url=excluded.url,
                image=excluded.image, brand=excluded.brand,
                category_slug=excluded.category_slug,
                category_title=excluded.category_title, group_key=excluded.group_key,
                display_ratio=excluded.display_ratio, display_price=excluded.display_price,
                price=excluded.price, old_price=excluded.old_price, stock=excluded.stock,
                online_only=excluded.online_only, rating=excluded.rating,
                rating_count=excluded.rating_count, last_seen=excluded.last_seen
            """,
            (
                branch_id, product.product_id, product.title, product.slug, product.url,
                product.image_url, product.brand, product.category_slug,
                product.category_title, product.group_key, product.display_ratio,
                product.display_price, product.price, product.old_price, product.stock,
                int(product.online_only), product.rating, product.rating_count, now, now,
            ),
        )

    def commit(self) -> None:
        self.conn.commit()

    def prune(self, days: int) -> int:
        """Забуває товари, яких давно не було в акції.

        Потрібно, щоб акція, яка повернулась через місяць, знову вважалась новою.
        """
        if days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        cur = self.conn.execute("DELETE FROM seen WHERE last_seen < ?", (cutoff,))
        self.conn.commit()
        return cur.rowcount
