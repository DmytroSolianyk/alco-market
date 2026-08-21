"""Складання гарних повідомлень для Telegram (HTML parse_mode)."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from .silpo import Product

# Емодзі підбираємо по слагу листової категорії — точніше, ніж по групі.
CATEGORY_EMOJI: tuple[tuple[str, str], ...] = (
    ("viski", "🥃"),
    ("koniak", "🥃"),
    ("brendi", "🥃"),
    ("rom", "🥃"),
    ("dzhyn", "🍸"),
    ("tekila", "🌵"),
    ("gorilka", "🍶"),
    ("nastoianky", "🍯"),
    ("likery", "🍯"),
    ("aziiskyi", "🍶"),
    ("vermuty", "🍸"),
    ("shampanske", "🍾"),
    ("igrysti", "🍾"),
    ("proseko", "🍾"),
    ("asti", "🍾"),
    ("lambrusko", "🍾"),
    ("kava-cava", "🍾"),
    ("pet-nat", "🍾"),
    ("vyna", "🍷"),
    ("vyno", "🍷"),
    ("sangriia", "🍷"),
    ("pyvo", "🍺"),
    ("brovarnia", "🍺"),
    ("sydr", "🍏"),
    ("alkoenergetyky", "⚡"),
    ("kokteili", "🍹"),
    ("drinksetter", "🍸"),
)

# Наскільки велика знижка — настільки гучніший маркер.
def _discount_badge(pct: float) -> str:
    if pct >= 50:
        return "🔥🔥"
    if pct >= 35:
        return "🔥"
    if pct >= 20:
        return "💥"
    return "🔻"


def emoji_for(product: Product) -> str:
    slug = product.category_slug
    for needle, emoji in CATEGORY_EMOJI:
        if needle in slug:
            return emoji
    return "🛒"


def money(value: float) -> str:
    """1234.5 -> '1 234,50'; 649.0 -> '649'."""
    rounded = round(value + 1e-9, 2)
    if abs(rounded - round(rounded)) < 0.005:
        text = f"{int(round(rounded)):,}".replace(",", " ")
    else:
        text = f"{rounded:,.2f}".replace(",", " ").replace(".", ",")
    return text


MONTHS_SHORT = (
    "січ", "лют", "бер", "кві", "тра", "чер",
    "лип", "сер", "вер", "жов", "лис", "гру",
)


def _day_label(when: datetime) -> str:
    today = datetime.now(timezone.utc).date()
    delta = (today - when.date()).days
    if delta <= 0:
        return "сьогодні"
    if delta == 1:
        return "вчора"
    return f"{when.day} {MONTHS_SHORT[when.month - 1]}"


def dynamics_block(points, current_price: float) -> list[str]:
    """Динаміка ціни: 899 (2 сер) → 799 (12 сер) → 649 (сьогодні)."""
    if not points or len(points) < 2:
        return []

    parts = []
    for point in points:
        mark = "🔖" if point.on_promo else ""
        parts.append(f"{money(point.price)}{mark} <i>({_day_label(point.when)})</i>")

    first, last = points[0].price, points[-1].price
    if last < first:
        arrow, verdict = "📉", "дешевшає"
    elif last > first:
        arrow, verdict = "📈", "дорожчає"
    else:
        arrow, verdict = "➡️", "без змін"

    change = abs(last - first) / first * 100 if first else 0
    tail = f" · {verdict}" + (f" на {change:.0f}%" if change >= 1 else "")
    return [f"{arrow} " + " → ".join(parts) + tail]


def history_block(v) -> list[str]:
    """Рядки про реальну ціну. Порожньо, поки історії замало."""
    if v is None:
        return []
    if not v.confident:
        if v.observed_days > 0:
            return [f"📊 <i>історія цін: {v.observed_days} дн. — ще збираю</i>"]
        return []

    lines = []
    if v.regular_price:
        lines.append(
            f"📊 звичайна ціна {money(v.regular_price)} ₴ → "
            f"<b>реальна знижка −{v.honest_discount:.0f}%</b>"
        )
    if v.inflated:
        lines.append("⚠️ <b>перекреслену ціну підняли перед акцією</b>")
    return lines


def caption(product: Product, branch_label: str, reason: str, verdict=None,
            points=None) -> str:
    """Підпис під фото. Telegram обмежує caption 1024 символами."""
    e = escape
    pct = product.discount_percent
    lines: list[str] = []

    headers = {
        "cheaper": "📉 <i>ціна впала ще нижче</i>\n",
        "top1": "👑 <b>Найвигідніша ціна на алкоголь</b>\n\n",
        "top1_cheaper": "👑 <b>Найвигідніша ціна на алкоголь</b>\n📉 <i>той самий товар, ціна змінилась</i>\n\n",
    }
    header = headers.get(reason, "")

    title = product.title
    if product.brand and product.brand.casefold() not in title.casefold():
        title = f"{title} · {product.brand}"

    lines.append(f"{header}{emoji_for(product)} <b>{e(title)}</b>")

    price_line = f"<b>{money(product.price)} ₴</b>  <s>{money(product.old_price)} ₴</s>"
    if pct >= 1:
        price_line += f"  {_discount_badge(pct)} <b>−{pct:.0f}%</b>"
    lines.append(price_line)

    extras: list[str] = []
    if product.saving >= 1:
        extras.append(f"економія {money(product.saving)} ₴")
    if product.display_ratio:
        # displayPrice — це ціна за одиницю displayRatio (напр. 649 ₴ за 1л).
        if abs(product.display_price - product.price) > 0.01:
            extras.append(f"{money(product.display_price)} ₴/{e(product.display_ratio)}")
        else:
            extras.append(e(product.display_ratio))
    if extras:
        lines.append("💰 " + " · ".join(extras))

    lines.extend(history_block(verdict))
    lines.extend(dynamics_block(points or [], product.price))

    meta: list[str] = [e(product.category_title)]
    if product.promo_labels:
        meta.extend(e(p) for p in product.promo_labels)
    lines.append("🏷 " + " · ".join(meta))

    ratings: list[str] = []
    if product.rating >= 1 and product.rating_count:
        ratings.append(f"⭐ {product.rating:.1f} ({product.rating_count})")
    if product.vivino:
        ratings.append(f"🍇 Vivino {float(product.vivino):.1f}")
    if product.untappd:
        ratings.append(f"🍻 Untappd {float(product.untappd):.1f}")
    if ratings:
        lines.append(" · ".join(ratings))

    lines.append(f"🏪 {e(branch_label)}")

    text = "\n".join(lines)
    return text[:1020] + "…" if len(text) > 1024 else text


def plural(count: int, one: str, few: str, many: str) -> str:
    """Українське відмінювання: 1 акція, 2-4 акції, 5-20 акцій, 21 акція…"""
    if count % 100 in range(11, 15):
        return many
    last = count % 10
    if last == 1:
        return one
    if last in (2, 3, 4):
        return few
    return many


def run_header(count: int, branch_label: str, extra: int = 0) -> str:
    word = plural(count, "нова акція", "нові акції", "нових акцій")
    text = f"🍾 <b>{count} {word}</b> на алкоголь\n🏪 {escape(branch_label)}"
    if extra:
        tail = plural(extra, "позиція", "позиції", "позицій")
        text += f"\n<i>показую топ за знижкою, ще {extra} {tail} — у застосунку Сільпо</i>"
    return text


def fallback_text(product: Product, branch_label: str, reason: str, verdict=None,
                  points=None) -> str:
    """Якщо Telegram не прийняв фото — шлемо той самий текст без картинки."""
    return (
        caption(product, branch_label, reason, verdict, points)
        + f"\n\n<a href=\"{product.url}\">Відкрити в Сільпо</a>"
    )
