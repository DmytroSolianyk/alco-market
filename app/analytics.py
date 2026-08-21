"""Запити для дашборду. Тільки читання — писати сюди нічого не можна."""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from . import history


SORT_OPTIONS = (
    ("", "За знижкою"),
    ("drop", "Найбільше подешевшало"),
    ("price_asc", "Спочатку дешевші"),
    ("price_desc", "Спочатку дорожчі"),
    ("name", "За назвою"),
)

GAP_SORT_OPTIONS = (
    ("", "За різницею, %"),
    ("gap_uah", "За різницею, ₴"),
    ("price_asc", "Спочатку дешевші"),
    ("price_desc", "Спочатку дорожчі"),
)


def _sort_products(rows: list[dict], sort: str) -> list[dict]:
    if sort == "drop":
        return sorted(rows, key=lambda r: -(r.get("day_drop") or 0))
    if sort == "price_asc":
        return sorted(rows, key=lambda r: (r["price"], r.get("title") or ""))
    if sort == "price_desc":
        return sorted(rows, key=lambda r: (-r["price"], r.get("title") or ""))
    if sort == "name":
        return sorted(rows, key=lambda r: (r.get("title") or "").casefold())
    return sorted(rows, key=lambda r: (-r["score"], r["price"]))


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def categories(conn, mode: str = "list", discounts_only: bool = True) -> list[dict]:
    """Категорії для випадайки.

    Число рахується ТИМ САМИМ фільтром, що й список на сторінці. Інакше
    у випадайці «155», а в списку 45 — і це справедливо читається як брехня.
    """
    base = "category_slug IS NOT NULL AND category_slug <> '' AND stock > 0"

    if mode == "gap":
        sql = f"""
            SELECT a.category_slug AS slug, MIN(a.category_title) AS title,
                   MIN(a.group_key) AS group_key, COUNT(*) AS n
            FROM products a JOIN products b
              ON a.product_id = b.product_id AND a.branch_id < b.branch_id
            WHERE a.price > 0 AND b.price > 0 AND ABS(a.price - b.price) > 0.01
              AND a.stock > 0 AND b.stock > 0
              AND a.category_slug IS NOT NULL AND a.category_slug <> ''
            GROUP BY a.category_slug ORDER BY MIN(a.group_key), title
        """
    elif mode == "fake":
        sql = f"""
            SELECT category_slug AS slug, MIN(category_title) AS title,
                   MIN(group_key) AS group_key, COUNT(DISTINCT product_id) AS n
            FROM products WHERE {base} AND inflated = 1 AND confident = 1
            GROUP BY category_slug ORDER BY group_key, title
        """
    else:
        extra = " AND old_price > price" if discounts_only else ""
        sql = f"""
            SELECT category_slug AS slug, MIN(category_title) AS title,
                   MIN(group_key) AS group_key, COUNT(DISTINCT product_id) AS n
            FROM products WHERE {base}{extra}
            GROUP BY category_slug ORDER BY group_key, title
        """
    return [row for row in _rows(conn, sql) if row["n"]]


def branches(conn) -> list[dict]:
    return _rows(
        conn,
        "SELECT branch_id, COUNT(*) AS products, "
        "       SUM(CASE WHEN stock > 0 THEN 1 ELSE 0 END) AS in_stock "
        "FROM products GROUP BY branch_id",
    )


def overview(conn, labels: dict[str, str]) -> dict:
    total = conn.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
    discounted = conn.execute(
        "SELECT COUNT(*) n FROM products WHERE old_price > price AND price > 0"
    ).fetchone()["n"]
    since = conn.execute("SELECT MIN(first_seen) s FROM price_history").fetchone()["s"]
    changes = conn.execute("SELECT COUNT(*) n FROM price_history").fetchone()["n"]

    days = 0
    if since:
        days = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(since)).days)

    depths = [
        (r["old_price"] - r["price"]) / r["old_price"] * 100
        for r in _rows(
            conn,
            "SELECT price, old_price FROM products WHERE old_price > price AND price > 0",
        )
    ]

    per_branch = []
    for row in branches(conn):
        per_branch.append(
            {
                "branch_id": row["branch_id"],
                "label": labels.get(row["branch_id"], row["branch_id"][:8]),
                "products": row["products"],
                "in_stock": row["in_stock"] or 0,
            }
        )

    return {
        "products": total,
        "discounted": discounted,
        "median_depth": round(statistics.median(depths), 1) if depths else 0.0,
        "history_days": days,
        "price_changes": changes,
        "branches": per_branch,
    }


def _decorate(conn, row: dict) -> dict:
    """Додає до товару вердикт про накрутку і точки динаміки."""
    verdict = history.verdict(
        conn, row["branch_id"], row["product_id"], row["price"], row["old_price"] or 0
    )
    row["claimed"] = round(verdict.claimed_discount, 1)
    row["regular_price"] = verdict.regular_price
    row["honest"] = round(verdict.honest_discount, 1) if verdict.honest_discount else None
    row["inflated"] = verdict.inflated
    row["confident"] = verdict.confident
    row["observed_days"] = verdict.observed_days
    row["score"] = round(verdict.effective_discount, 1)
    return row


PAGE_SIZE = 48


def _clamp(offset: int, total: int, limit: int) -> int:
    """Не даємо провалитись за останню сторінку — інакше ?page=999
    віддає порожній список без жодного пояснення."""
    if total <= 0:
        return 0
    return max(0, min(offset, ((total - 1) // limit) * limit))


def _attach_stores(conn, rows: list[dict], labels: dict[str, str]) -> list[dict]:
    """Дописує до кожного товару перелік магазинів, де він є."""
    if not rows:
        return rows
    ids = {r["product_id"] for r in rows}
    marks = ",".join("?" * len(ids))
    stores: dict[str, list[str]] = {}
    for row in conn.execute(
        f"SELECT product_id, branch_id FROM products WHERE product_id IN ({marks})",
        tuple(ids),
    ):
        stores.setdefault(row["product_id"], []).append(
            labels.get(row["branch_id"], "")
        )
    for row in rows:
        found = [s for s in stores.get(row["product_id"], []) if s]
        row["branch_labels"] = found
        row["branch_label"] = labels.get(row["branch_id"], "")
    return rows


# Як згортати дублі одного товару з двох магазинів і як його впорядкувати.
_PICK = {
    "":          ("MAX(score)",    "_pick DESC, price ASC"),
    "drop":      ("MAX(day_drop)", "_pick DESC, price ASC"),
    "price_asc": ("MIN(price)",    "_pick ASC, title ASC"),
    "price_desc":("MAX(price)",    "_pick DESC, title ASC"),
    "name":      ("MIN(price)",    "title ASC"),
}


def top_discounts(conn, labels: dict[str, str], limit: int = PAGE_SIZE, offset: int = 0,
                  query: str = "", category: str = "", sort: str = "",
                  discounts_only: bool = True) -> dict:
    """Знижки з пагінацією.

    Порядок і згортання дублів робить SQL по збереженому score — тому
    вибірка більше не обмежена кількома десятками рядків.
    """
    where, params = ["price > 0", "stock > 0"], []
    if query:
        where.append("ulower(title) LIKE ulower(?)")
        params.append(f"%{query}%")
    if discounts_only:
        where.append("old_price > price")
    if category:
        where.append("category_slug = ?")
        params.append(category)
    if sort == "drop":
        where.append("day_drop > 0")
    clause = " AND ".join(where)

    total = conn.execute(
        f"SELECT COUNT(DISTINCT product_id) AS n FROM products WHERE {clause}",
        tuple(params),
    ).fetchone()["n"]

    offset = _clamp(offset, total, limit)
    pick, order = _PICK.get(sort, _PICK[""])
    rows = _rows(
        conn,
        f"SELECT *, {pick} AS _pick FROM products WHERE {clause} "
        f"GROUP BY product_id ORDER BY {order} LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    )
    return {"rows": _attach_stores(conn, rows, labels), "total": total, "offset": offset}


def cross_store(conn, labels: dict[str, str], limit: int = PAGE_SIZE, offset: int = 0,
                query: str = "", category: str = "", sort: str = "") -> dict:
    """Той самий товар, різна ціна в двох магазинах."""
    where, params = [], []
    if query:
        where.append("AND ulower(a.title) LIKE ulower(?)")
        params.append(f"%{query}%")
    if category:
        where.append("AND a.category_slug = ?")
        params.append(category)
    extra = " ".join(where)

    base = f"""
        FROM products a
        JOIN products b
          ON a.product_id = b.product_id
         AND a.branch_id < b.branch_id
        WHERE a.price > 0 AND b.price > 0
          AND ABS(a.price - b.price) > 0.01
          AND a.stock > 0 AND b.stock > 0
          {extra}
    """
    total = conn.execute(f"SELECT COUNT(*) AS n {base}", tuple(params)).fetchone()["n"]

    offset = _clamp(offset, total, limit)
    order = {
        "gap_uah": "ABS(a.price - b.price) DESC",
        "price_asc": "MIN(a.price, b.price) ASC",
        "price_desc": "MIN(a.price, b.price) DESC",
    }.get(sort, "ABS(a.price - b.price) / MIN(a.price, b.price) DESC")

    rows = _rows(
        conn,
        f"""SELECT a.product_id, a.title, a.image, a.slug, a.url, a.display_ratio,
                   a.category_title,
                   a.branch_id AS b1, a.price AS p1, a.stock AS s1,
                   b.branch_id AS b2, b.price AS p2, b.stock AS s2
            {base} ORDER BY {order} LIMIT ? OFFSET ?""",
        tuple(params) + (limit, offset),
    )
    for row in rows:
        cheap_first = row["p1"] <= row["p2"]
        row["cheaper_branch"] = labels.get(row["b1"] if cheap_first else row["b2"], "")
        row["dearer_branch"] = labels.get(row["b2"] if cheap_first else row["b1"], "")
        row["cheap_price"] = min(row["p1"], row["p2"])
        row["dear_price"] = max(row["p1"], row["p2"])
        row["gap"] = round(row["dear_price"] - row["cheap_price"], 2)
        # Від ДОРОЖЧОЇ ціни: «економія 48%» — це те, скільки не віддаси,
        # якщо купиш у дешевшому магазині.
        row["gap_percent"] = round(row["gap"] / row["dear_price"] * 100, 1)
    return {"rows": rows, "total": total, "offset": offset}


def fakes(conn, labels: dict[str, str], limit: int = PAGE_SIZE, offset: int = 0,
          query: str = "", category: str = "", sort: str = "") -> dict:
    """Товари, де перекреслену ціну підняли перед акцією.

    Фільтр тепер у SQL, а не перебором чотирьохсот найглибших знижок —
    тож накрутка на дрібній знижці більше не випадає з поля зору.
    """
    where = ["inflated = 1", "confident = 1", "price > 0"]
    params: list = []
    if query:
        where.append("ulower(title) LIKE ulower(?)")
        params.append(f"%{query}%")
    if category:
        where.append("category_slug = ?")
        params.append(category)
    clause = " AND ".join(where)

    total = conn.execute(
        f"SELECT COUNT(DISTINCT product_id) AS n FROM products WHERE {clause}",
        tuple(params),
    ).fetchone()["n"]

    offset = _clamp(offset, total, limit)
    pick, order = _PICK.get(sort, ("MAX(claimed - COALESCE(honest, 0))",
                                   "_pick DESC, price ASC"))
    rows = _rows(
        conn,
        f"SELECT *, {pick} AS _pick FROM products WHERE {clause} "
        f"GROUP BY product_id ORDER BY {order} LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    )
    for row in rows:
        row["overstated"] = round((row["claimed"] or 0) - (row["honest"] or 0), 1)
    return {"rows": _attach_stores(conn, rows, labels), "total": total, "offset": offset}


def _price_on(rows: list[dict], moment: datetime) -> float | None:
    """Ціна товару на конкретний момент за записами про зміни."""
    active = None
    for row in rows:
        start = datetime.fromisoformat(row["first_seen"])
        end = datetime.fromisoformat(row["last_seen"])
        if start <= moment <= end:
            active = row["price"]
        elif start <= moment:
            active = row["price"]
    return active


def category_index(conn, days: int = 30) -> dict:
    """Медіанна ціна по групах категорій у часі — «інфляція» на поличці.

    Медіана, а не середнє: одна пляшка за 12 000 ₴ не має рухати індекс.
    """
    groups = _rows(
        conn,
        "SELECT DISTINCT group_key FROM products WHERE group_key IS NOT NULL "
        "AND group_key <> '' ORDER BY group_key",
    )
    history_rows = _rows(
        conn,
        "SELECT branch_id, product_id, price, first_seen, last_seen FROM price_history",
    )
    by_product: dict[tuple, list[dict]] = {}
    for row in history_rows:
        by_product.setdefault((row["branch_id"], row["product_id"]), []).append(row)

    membership = _rows(conn, "SELECT branch_id, product_id, group_key FROM products")
    by_group: dict[str, list[tuple]] = {}
    for row in membership:
        by_group.setdefault(row["group_key"], []).append(
            (row["branch_id"], row["product_id"])
        )

    today = datetime.now(timezone.utc)
    stamps = [today - timedelta(days=d) for d in range(days - 1, -1, -1)]

    series = {}
    for group in groups:
        key = group["group_key"]
        points = []
        for stamp in stamps:
            prices = []
            for ident in by_group.get(key, []):
                price = _price_on(by_product.get(ident, []), stamp)
                if price:
                    prices.append(price)
            points.append(
                {
                    "date": stamp.date().isoformat(),
                    "median": round(statistics.median(prices), 2) if prices else None,
                    "count": len(prices),
                }
            )
        series[key] = points
    return {"labels": [s.date().isoformat() for s in stamps], "series": series}


def product_detail(conn, branch_id: str, product_id: str, labels: dict[str, str]) -> dict | None:
    row = conn.execute(
        "SELECT * FROM products WHERE branch_id = ? AND product_id = ?",
        (branch_id, product_id),
    ).fetchone()
    if row is None:
        return None
    item = _decorate(conn, dict(row))
    item["branch_label"] = labels.get(branch_id, "")

    item["series"] = _rows(
        conn,
        "SELECT price, on_promo, first_seen, last_seen FROM price_history "
        "WHERE branch_id = ? AND product_id = ? ORDER BY first_seen",
        (branch_id, product_id),
    )

    # Той самий товар в іншому магазині — для порівняння поруч.
    item["elsewhere"] = [
        {**dict(r), "branch_label": labels.get(r["branch_id"], "")}
        for r in conn.execute(
            "SELECT branch_id, price, old_price, stock FROM products "
            "WHERE product_id = ? AND branch_id <> ?",
            (product_id, branch_id),
        ).fetchall()
    ]
    return item
