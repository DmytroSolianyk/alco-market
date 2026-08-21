"""Клієнт до публічного storefront API Сільпо.

Авторизація не потрібна — це той самий бекенд, який живить silpo.ua і MCP-конектор.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests

log = logging.getLogger(__name__)

API_BASE = "https://sf-ecom-api.silpo.ua/v1/uk"
IMAGE_BASE = "https://images.silpo.ua/v2/products/{size}/webp/{name}"
PRODUCT_URL = "https://silpo.ua/product/{slug}"
PAGE_SIZE = 200

# Людські назви промо-механік (promotions[].id).
PROMO_LABELS = {
    "cinotyzhyky": "Цінотижні",
    "cinodidjiky": "Цінодідьки",
    "kupuy_ta_zaoshadjuy": "Купуй та заощаджуй",
    "znovu-v-shkolu": "Знову в школу",
    "back-to-school": "Знову в школу",
    "only_online": "🌐 тільки онлайн",
    "vlasnyi-import": "Власний імпорт",
    "vlasna-marka": "Власна марка",
    "plius-na-zhyttia": "Плюс на життя",
}

# 'additional' стоїть майже на кожній акційній позиції й читачу нічого не каже.
PROMO_SKIP = {"additional"}


@dataclass(frozen=True)
class Product:
    """Акційний товар, зведений до полів, які нам реально потрібні."""

    product_id: str
    title: str
    slug: str
    price: float
    old_price: float
    ratio: str
    display_ratio: str
    display_price: float
    display_old_price: float
    stock: float
    brand: str
    image_url: str
    url: str
    category_slug: str
    category_title: str
    group_key: str
    promo_labels: tuple[str, ...]
    online_only: bool
    rating: float
    rating_count: int
    vivino: float | None
    untappd: float | None

    @property
    def discount_percent(self) -> float:
        if not self.old_price or self.old_price <= self.price:
            return 0.0
        return (self.old_price - self.price) / self.old_price * 100.0

    @property
    def saving(self) -> float:
        return max(0.0, self.old_price - self.price)

    @property
    def rank_key(self) -> tuple:
        """Детермінований порядок «найвигідніше першим».

        product_id у кінці — щоб при однаковій знижці лідер не стрибав
        між рівноцінними позиціями від проходу до проходу.
        """
        return (-round(self.discount_percent, 4), -self.saving, self.product_id)


class SilpoError(RuntimeError):
    pass


class SilpoBadRequest(SilpoError):
    """4xx — запит невалідний, повторювати немає сенсу."""


class SilpoClient:
    def __init__(self, timeout: int = 30, image_size: str = "500x500") -> None:
        self.timeout = timeout
        self.image_size = image_size
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "silpo-alco-watch/1.0 (+self-hosted promo monitor)",
            }
        )
        self._category_cache: dict[str, list[dict[str, Any]]] = {}

    # ---------------------------------------------------------------- helpers

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise SilpoError(f"HTTP {resp.status_code} для {url}")
                # 4xx — це наша помилка (кривий branchId/слаг): ретрай не поможе.
                if 400 <= resp.status_code < 500:
                    raise SilpoBadRequest(f"HTTP {resp.status_code} для {url}: {resp.text[:200]}")
                resp.raise_for_status()
                return resp.json()
            except SilpoBadRequest:
                raise
            except Exception as exc:  # мережа/5xx/429 — ретраїмо з бекофом
                last_error = exc
                sleep_for = 2 ** attempt
                log.warning("Silpo %s спроба %d невдала (%s), пауза %ss", path, attempt + 1, exc, sleep_for)
                time.sleep(sleep_for)
        raise SilpoError(f"Silpo API недоступний: {path}") from last_error

    # --------------------------------------------------------------- branches

    def search_branches(self, query: str = "", limit: int = 500) -> list[dict[str, Any]]:
        """Усі філії; за потреби фільтрує по місту/адресі (регістронезалежно)."""
        data = self._get("/branches", {"limit": limit})
        items = data.get("items", [])
        if not query:
            return items
        needle = query.casefold()
        return [
            b
            for b in items
            if needle in f"{b.get('cityFull', '')} {b.get('addressFull', '')}".casefold()
        ]

    # ------------------------------------------------------------- categories

    def categories(self, branch_id: str, delivery_type: str) -> list[dict[str, Any]]:
        if branch_id not in self._category_cache:
            data = self._get(
                f"/branches/{branch_id}/categories",
                {"deliveryType": delivery_type, "depthLevel": 2},
            )
            self._category_cache[branch_id] = data.get("items", [])
        return self._category_cache[branch_id]

    def leaf_categories(
        self, branch_id: str, delivery_type: str, root_slug: str
    ) -> list[dict[str, Any]]:
        """Листові категорії піддерева `root_slug`.

        API віддає товари тільки для листових категорій: запит по проміжній
        (напр. `mitsnyi-alkogol-4458`) стабільно повертає total=0.
        """
        items = self.categories(branch_id, delivery_type)
        by_parent: dict[str, list[dict[str, Any]]] = {}
        for cat in items:
            by_parent.setdefault(cat.get("parentId") or "", []).append(cat)

        roots = [c for c in items if c.get("slug") == root_slug]
        if not roots:
            log.warning("Категорію %s не знайдено у філії %s", root_slug, branch_id)
            return []

        leaves: list[dict[str, Any]] = []
        stack = list(roots)
        while stack:
            node = stack.pop()
            children = by_parent.get(node["id"], [])
            if children:
                stack.extend(children)
            else:
                leaves.append(node)
        return leaves

    # --------------------------------------------------------------- products

    def promo_products(
        self,
        branch_id: str,
        delivery_type: str,
        category_slug: str,
    ) -> Iterable[dict[str, Any]]:
        return self.products(branch_id, delivery_type, category_slug, promo_only=True)

    def products(
        self,
        branch_id: str,
        delivery_type: str,
        category_slug: str,
        promo_only: bool = True,
    ) -> Iterable[dict[str, Any]]:
        offset = 0
        while True:
            params: dict[str, Any] = {
                "limit": PAGE_SIZE,
                "offset": offset,
                "deliveryType": delivery_type,
                "category": category_slug,
            }
            if promo_only:
                params["mustHavePromotion"] = "true"
            data = self._get(f"/branches/{branch_id}/products", params)
            items = data.get("items", [])
            yield from items
            offset += len(items)
            if not items or offset >= int(data.get("total", 0)):
                return

    # ---------------------------------------------------------------- mapping

    def to_product(
        self,
        raw: dict[str, Any],
        category_slug: str,
        category_title: str,
        group_key: str,
    ) -> Product:
        icon = raw.get("icon") or ""
        image_url = (
            IMAGE_BASE.format(size=self.image_size, name=icon) if icon else ""
        )
        labels: list[str] = []
        online_only = False
        for promo in raw.get("promotions") or []:
            if promo.get("id") == "only_online":
                online_only = True
            promo_id = promo.get("id") or ""
            # type == "set" — це каталожна добірка, а не механіка знижки.
            if not promo_id or promo_id in PROMO_SKIP or promo.get("type") != "promo":
                continue
            labels.append(
                PROMO_LABELS.get(promo_id, promo_id.replace("_", " ").replace("-", " ").capitalize())
            )
        return Product(
            product_id=str(raw.get("id") or raw.get("slug") or ""),
            title=(raw.get("title") or "").strip(),
            slug=raw.get("slug") or "",
            price=float(raw.get("price") or 0),
            old_price=float(raw.get("oldPrice") or 0),
            ratio=raw.get("ratio") or "",
            display_ratio=raw.get("displayRatio") or "",
            display_price=float(raw.get("displayPrice") or raw.get("price") or 0),
            display_old_price=float(raw.get("displayOldPrice") or raw.get("oldPrice") or 0),
            stock=float(raw.get("stock") or 0),
            brand=(raw.get("brandTitle") or "").strip(),
            image_url=image_url,
            url=PRODUCT_URL.format(slug=raw.get("slug") or ""),
            category_slug=category_slug,
            category_title=category_title,
            group_key=group_key,
            promo_labels=tuple(labels),
            online_only=online_only,
            rating=float(raw.get("guestProductRating") or 0),
            rating_count=int(raw.get("guestProductRatingCount") or 0),
            vivino=raw.get("vivinoRating"),
            untappd=raw.get("untappdRating"),
        )
