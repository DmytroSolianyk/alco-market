"""Товар + усе, що ми знаємо про його ціну. Спільне для розсилки і команд."""
from __future__ import annotations

from dataclasses import dataclass

from . import history
from .silpo import Product


@dataclass
class Candidate:
    product: Product
    verdict: object | None          # history.PriceVerdict
    points: list                    # history.PricePoint
    previous_price: float | None
    status: str                     # new | same | changed

    @property
    def day_drop(self) -> float:
        """Наскільки подешевшав відносно попередньої НАШОЇ ціни, у %."""
        if not self.previous_price or self.previous_price <= self.product.price:
            return 0.0
        return (self.previous_price - self.product.price) / self.previous_price * 100.0

    @property
    def rising(self) -> bool:
        return bool(self.previous_price and self.product.price > self.previous_price)

    @property
    def score(self) -> float:
        """Знижка, якій можна вірити.

        Пріоритет — власним вимірам: падіння відносно ціни, яку ми самі
        бачили на полиці. Плашка Сільпо йде в хід лише поки історії немає.
        """
        if self.verdict is not None and self.verdict.confident:
            return max(self.verdict.honest_discount or 0.0, self.day_drop)
        if self.day_drop > 0:
            return self.day_drop
        return self.product.discount_percent

    @property
    def sort_key(self) -> tuple:
        return (-self.score, -self.product.saving, self.product.product_id)


def build_candidate(conn, branch_id: str, product: Product, points: int = 4,
          status: str = "same") -> Candidate:
    return Candidate(
        product=product,
        verdict=history.verdict(
            conn, branch_id, product.product_id, product.price, product.old_price
        ),
        points=history.recent_points(conn, branch_id, product.product_id, points),
        previous_price=history.previous_price(conn, branch_id, product.product_id),
        status=status,
    )


def group_identical(pairs: list[tuple]) -> list[tuple]:
    """[(branch, candidate)] -> [(candidate, [labels])].

    Якщо в кількох магазинах виграв той самий товар за тією ж ціною —
    це одне повідомлення з переліком магазинів, а не два однакових.
    Різна ціна означає різні пропозиції, тому вони лишаються окремо.
    """
    grouped: dict[tuple, list] = {}
    order: list[tuple] = []
    for branch, candidate in pairs:
        key = (candidate.product.product_id, round(candidate.product.price, 2))
        if key not in grouped:
            grouped[key] = [candidate, []]
            order.append(key)
        grouped[key][1].append(branch.label)
    return [(grouped[k][0], grouped[k][1]) for k in order]


def branches_label(labels: list[str]) -> str:
    return " · ".join(labels)
