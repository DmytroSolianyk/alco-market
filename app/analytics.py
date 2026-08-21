"""Запити для дашборду. Тільки читання — писати сюди нічого не можна."""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from . import history


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


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


def top_discounts(conn, labels: dict[str, str], limit: int = 10,
                  branch_id: str | None = None) -> list[dict]:
    """Найглибші знижки. Ранжуємо за реальною, коли історія дозволяє."""
    sql = (
        "SELECT * FROM products WHERE old_price > price AND price > 0 AND stock > 0"
    )
    params: tuple = ()
    if branch_id:
        sql += " AND branch_id = ?"
        params = (branch_id,)
    # Беремо із запасом: остаточний порядок задає реальна знижка, а не заявлена.
    sql += " ORDER BY (old_price - price) / old_price DESC LIMIT ?"
    rows = _rows(conn, sql, params + (limit * 6,))

    decorated = [_decorate(conn, r) for r in rows]
    decorated.sort(key=lambda r: (-r["score"], r["price"]))

    # Той самий товар є в обох магазинах — у топі він має бути один раз,
    # інакше десятка наполовину складається з дублів.
    best: dict[str, dict] = {}
    for row in decorated:
        row["branch_label"] = labels.get(row["branch_id"], "")
        keep = best.get(row["product_id"])
        if keep is None:
            row["branch_labels"] = [row["branch_label"]]
            best[row["product_id"]] = row
        elif row["branch_label"] not in keep["branch_labels"]:
            keep["branch_labels"].append(row["branch_label"])

    out = sorted(best.values(), key=lambda r: (-r["score"], r["price"]))
    return out[:limit]


def cross_store(conn, labels: dict[str, str], limit: int = 20) -> list[dict]:
    """Той самий товар, різна ціна в двох магазинах."""
    rows = _rows(
        conn,
        """
        SELECT a.product_id, a.title, a.image, a.slug, a.url, a.display_ratio,
               a.category_title,
               a.branch_id AS b1, a.price AS p1, a.stock AS s1,
               b.branch_id AS b2, b.price AS p2, b.stock AS s2
        FROM products a
        JOIN products b
          ON a.product_id = b.product_id
         AND a.branch_id < b.branch_id
        WHERE a.price > 0 AND b.price > 0
          AND ABS(a.price - b.price) > 0.01
          AND a.stock > 0 AND b.stock > 0
        ORDER BY ABS(a.price - b.price) / MIN(a.price, b.price) DESC
        LIMIT ?
        """,
        (limit,),
    )
    for row in rows:
        cheap_first = row["p1"] <= row["p2"]
        row["cheaper_branch"] = labels.get(row["b1"] if cheap_first else row["b2"], "")
        row["dearer_branch"] = labels.get(row["b2"] if cheap_first else row["b1"], "")
        row["cheap_price"] = min(row["p1"], row["p2"])
        row["dear_price"] = max(row["p1"], row["p2"])
        row["gap"] = round(row["dear_price"] - row["cheap_price"], 2)
        # Від ДОРОЖЧОЇ ціни: «економія 48%» — це те, скільки не віддаси,
        # якщо купиш у дешевшому магазині. Від дешевшої вийшло б 92%,
        # що читається як «знижка 92%» і вводить в оману.
        row["gap_percent"] = round(row["gap"] / row["dear_price"] * 100, 1)
    return rows


def fakes(conn, labels: dict[str, str], limit: int = 20) -> list[dict]:
    """Товари, де перекреслену ціну підняли перед акцією."""
    rows = _rows(
        conn,
        "SELECT * FROM products WHERE old_price > price AND price > 0 "
        "ORDER BY (old_price - price) / old_price DESC LIMIT 400",
    )
    caught = []
    for row in rows:
        decorated = _decorate(conn, row)
        if decorated["inflated"] and decorated["confident"]:
            decorated["branch_label"] = labels.get(row["branch_id"], "")
            decorated["overstated"] = round(
                decorated["claimed"] - (decorated["honest"] or 0), 1
            )
            caught.append(decorated)
    caught.sort(key=lambda r: -r["overstated"])
    return caught[:limit]


def movers(conn, labels: dict[str, str], limit: int = 10, direction: str = "up") -> list[dict]:
    """Хто найбільше подорожчав або подешевшав відносно попередньої ціни."""
    out = []
    for row in _rows(conn, "SELECT * FROM products WHERE price > 0 AND stock > 0"):
        previous = history.previous_price(conn, row["branch_id"], row["product_id"])
        if not previous or previous <= 0:
            continue
        change = (row["price"] - previous) / previous * 100
        if direction == "up" and change <= 0.5:
            continue
        if direction == "down" and change >= -0.5:
            continue
        decorated = _decorate(conn, row)
        decorated["previous_price"] = previous
        decorated["change"] = round(change, 1)
        decorated["branch_label"] = labels.get(row["branch_id"], "")
        out.append(decorated)
    out.sort(key=lambda r: -abs(r["change"]))
    return out[:limit]


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


def search(conn, query: str, labels: dict[str, str], limit: int = 40) -> list[dict]:
    rows = _rows(
        conn,
        "SELECT * FROM products WHERE title LIKE ? ORDER BY title LIMIT ?",
        (f"%{query}%", limit),
    )
    out = []
    for row in rows:
        decorated = _decorate(conn, row)
        decorated["branch_label"] = labels.get(row["branch_id"], "")
        out.append(decorated)
    return out


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
