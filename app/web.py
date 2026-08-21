"""Вбудований веб-дашборд. Стандартна бібліотека — нових залежностей немає."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote_plus, urlparse

from . import analytics, webui
from .config import Config
from .state import register_functions
from .formatting import money

log = logging.getLogger(__name__)

_local = threading.local()


def _connect(path: str) -> sqlite3.Connection:
    """Читальне зʼєднання на потік. Тільки read-only — дашборд нічого не пише."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        register_functions(conn)
        _local.conn = conn
    return conn


class Dashboard:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.labels = {b.branch_id: b.label for b in cfg.branches}

    # ------------------------------------------------------------- сторінки

    @staticmethod
    def _carry(query: str, category: str, sort: str) -> str:
        """Те, що має пережити перемикання вкладки."""
        parts = []
        if query:
            parts.append(f"q={quote_plus(query)}")
        if category:
            parts.append(f"cat={quote_plus(category)}")
        if sort:
            parts.append(f"sort={quote_plus(sort)}")
        return "&".join(parts)

    def _shell(self, conn, action: str, query: str, category: str, sort: str,
               sort_options) -> dict:
        return {
            "search_action": action,
            "search_value": query,
            "filters": webui.filter_bar(
                analytics.categories(conn), category, sort_options, sort),
            "carry": self._carry(query, category, sort),
        }

    def home(self, conn, query: str = "", category: str = "", sort: str = "") -> str:
        shell = self._shell(conn, "/", query, category, sort, analytics.SORT_OPTIONS)
        narrowed = bool(query or category or sort)
        found = analytics.top_discounts(
            conn, self.labels, limit=60 if narrowed else 10,
            query=query, category=category, sort=sort)

        if narrowed:
            what = f'{len(found)}{"+" if len(found) >= 60 else ""} товарів'
            if query:
                what += f' за «{webui.e(query)}»'
            inner = (
                f'<div class="cards">{"".join(webui.product_card(r) for r in found)}</div>'
                if found else webui.empty(
                    "Нічого не знайшлось",
                    "Спробуй коротше слово або зніми фільтр категорії.")
            )
            body = f'<section><h2>Знайдено<span class="hint">{what}</span></h2>{inner}</section>'
            return webui.page("Пошук", body, active="home", **shell)

        data = analytics.overview(conn, self.labels)
        body = [webui.kpis(data)]
        body.append(
            '<section><h2>Топ-10 знижок'
            '<span class="hint">за реальною знижкою, коли історії вистачає</span></h2>'
            + (f'<div class="cards">{"".join(webui.product_card(r) for r in found)}</div>'
               if found else webui.empty("Знижок не знайдено", "Перший обхід ще не завершився."))
            + "</section>"
        )
        gaps = analytics.cross_store(conn, self.labels, limit=6)
        body.append(
            '<section><h2>Де дешевше'
            '<span class="hint">той самий товар, різні магазини</span></h2>'
            + webui.gap_list(gaps)
            + '<a class="more" href="/cross-store">Показати всі →</a></section>'
        )
        caught = analytics.fakes(conn, self.labels, limit=5)
        if caught:
            body.append(
                '<section><h2>Зал ганьби<span class="hint">ціну підняли перед акцією</span></h2>'
                + f'<div class="cards">{"".join(webui.product_card(r) for r in caught)}</div>'
                + '<a class="more" href="/fakes">Показати всі →</a></section>'
            )
        else:
            body.append(
                '<section><h2>Зал ганьби</h2>'
                + webui.empty(
                    "Поки нікого не спіймали",
                    f"Щоб довести накрутку, потрібно бачити ціну до акції. "
                    f"Історії зараз {data['history_days']} дн., перші вироки — "
                    f"десь через два тижні.")
                + "</section>"
            )
        down = analytics.movers(conn, self.labels, limit=6, direction="down")
        if down:
            body.append(
                '<section><h2>Подешевшало з минулого замі́ру</h2>'
                + f'<div class="cards">{"".join(webui.product_card(r) for r in down)}</div></section>'
            )
        return webui.page("Огляд", "".join(body), active="home", **shell)

    def cross_store(self, conn, query: str = "", category: str = "", sort: str = "") -> str:
        shell = self._shell(conn, "/cross-store", query, category, sort,
                            analytics.GAP_SORT_OPTIONS)
        gaps = analytics.cross_store(conn, self.labels, limit=100,
                                     query=query, category=category, sort=sort)
        hint = f'{len(gaps)}{"+" if len(gaps) >= 100 else ""} товарів'
        if query:
            hint += f" за «{webui.e(query)}»"
        note = ('<p class="lead">Той самий товар коштує по-різному в двох магазинах.</p>'
                if not (query or category) else "")
        inner = webui.gap_list(gaps) if gaps else webui.empty(
            "Нічого не знайшлось",
            "Або товару немає в обох магазинах, або він коштує там однаково.")
        body = f'<section><h2>Де дешевше<span class="hint">{hint}</span></h2>{note}{inner}</section>'
        return webui.page("Де дешевше", body, active="gap", **shell)

    def fakes(self, conn, query: str = "", category: str = "", sort: str = "") -> str:
        shell = self._shell(conn, "/fakes", query, category, sort, analytics.SORT_OPTIONS)
        caught = analytics.fakes(conn, self.labels, limit=60,
                                 query=query, category=category, sort=sort)
        if caught:
            inner = f'<div class="cards">{"".join(webui.product_card(r) for r in caught)}</div>'
            hint = f'{len(caught)}{"+" if len(caught) >= 60 else ""} товарів'
        else:
            days = analytics.overview(conn, self.labels)["history_days"]
            inner = webui.empty(
                "Поки нікого не спіймали",
                f"Історії {days} дн. Накрутку видно тільки тоді, коли ми бачили ціну "
                f"і до акції, і під час неї — а акції тижневі.")
            hint = "перекреслену ціну підняли перед акцією"
        body = f'<section><h2>Зал ганьби<span class="hint">{hint}</span></h2>{inner}</section>'
        return webui.page("Зал ганьби", body, active="fake", **shell)

    def index(self, conn, query: str = "", category: str = "", sort: str = "") -> str:
        shell = self._shell(conn, "/index", query, category, sort, analytics.SORT_OPTIONS)
        data = analytics.category_index(conn, days=30)
        body = (
            '<section><h2>Індекс цін'
            '<span class="hint">медіанна ціна по категоріях, 30 днів</span></h2>'
            '<p class="lead">Медіана, а не середнє — одна пляшка за 12 000 ₴ '
            'не повинна рухати індекс.</p>'
            + webui.index_chart(data) + "</section>"
        )
        if query or category:
            found = analytics.top_discounts(conn, self.labels, limit=60,
                                            query=query, category=category, sort=sort)
            inner = (
                f'<div class="cards">{"".join(webui.product_card(r) for r in found)}</div>'
                if found else webui.empty("Нічого не знайшлось", "Спробуй інший запит.")
            )
            body += (f'<section><h2>Знайдено<span class="hint">{len(found)} товарів</span>'
                     f'</h2>{inner}</section>')
        return webui.page("Індекс цін", body, active="index", **shell)

    def product(self, conn, branch_id: str, product_id: str) -> str | None:
        item = analytics.product_detail(conn, branch_id, product_id, self.labels)
        if item is None:
            return None

        facts = [("Магазин", webui.e(item["branch_label"]))]
        if item.get("display_ratio"):
            facts.append(("Обʼєм", webui.e(item["display_ratio"])))
        if item.get("category_title"):
            facts.append(("Категорія", webui.e(item["category_title"])))
        if item.get("brand"):
            facts.append(("Бренд", webui.e(item["brand"])))
        facts.append(("Ціна зараз", f'{money(item["price"])} ₴'))
        if item.get("old_price") and item["old_price"] > item["price"]:
            facts.append(("Перекреслена", f'{money(item["old_price"])} ₴'))
            facts.append(("Заявлена знижка", f'−{item["claimed"]:.0f}%'))
        if item["confident"] and item["regular_price"]:
            facts.append(("Звичайна ціна", f'{money(item["regular_price"])} ₴'))
            facts.append(("Реальна знижка", f'−{item["honest"]:.0f}%'))
        else:
            facts.append(("Історія", f'{item["observed_days"]} дн. — ще збираю'))
        facts.append(("На полиці", "так" if item.get("stock", 0) > 0 else "немає"))
        if item.get("rating"):
            facts.append(("Оцінка", f'{item["rating"]:.1f} ({item.get("rating_count", 0)})'))

        facts_html = "".join(
            f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in facts
        )

        elsewhere = ""
        if item["elsewhere"]:
            rows = []
            for other in item["elsewhere"]:
                delta = other["price"] - item["price"]
                mark = (
                    f'<span class="pill real">дешевше на {money(abs(delta))} ₴</span>'
                    if delta < -0.01 else
                    f'<span class="pill claim">дорожче на {money(delta)} ₴</span>'
                    if delta > 0.01 else '<span class="pill soft">така сама</span>'
                )
                href = f'/product?b={other["branch_id"]}&p={product_id}'
                rows.append(
                    f'<div><dt><a href="{href}">{webui.e(other["branch_label"])}</a></dt>'
                    f'<dd>{money(other["price"])} ₴ {mark}</dd></div>'
                )
            elsewhere = f'<div class="facts">{"".join(rows)}</div>'

        img = (
            f'<img src="{webui.e(item["image"])}" alt="">'
            if item.get("image") else ""
        )
        warn = (
            '<div class="empty" style="border-color:var(--bad);color:var(--bad)">'
            "<b>⚠️ Перекреслену ціну підняли перед акцією</b>"
            "<span>Заявлена знижка більша за реальну.</span></div>"
            if item["inflated"] and item["confident"] else ""
        )
        silpo = (
            f'<div class="card-meta"><a href="{webui.e(item["url"])}" target="_blank" '
            f'rel="noopener">Відкрити в Сільпо →</a></div>' if item.get("url") else ""
        )

        body = f"""<section>
<div class="hero">{img}<div><h1>{webui.e(item["title"] or "")}</h1>
<div class="card-meta" style="margin-top:.5rem">{webui._discount_pills(item)}</div>
{silpo}</div></div>
{warn}
<div class="detail">
  <div>{webui.price_chart(item["series"])}</div>
  <div style="display:flex;flex-direction:column;gap:1rem">
    <div class="facts">{facts_html}</div>
    {elsewhere}
  </div>
</div></section>"""
        return webui.page(item["title"] or "Товар", body)


class Handler(BaseHTTPRequestHandler):
    dashboard: Dashboard
    db_path: str
    server_version = "alco-market"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s %s", self.address_string(), fmt % args)

    def _send(self, body: str, status: int = 200, ctype: str = "text/html; charset=utf-8") -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        # http.server декодує рядок запиту як latin-1 (так вимагає HTTP для
        # заголовків), тож кирилиця приходить мохіто-байтами. Проганяємо назад
        # у байти й читаємо як UTF-8 — це лагодить і сирі, і %-кодовані URL.
        raw = self.path.encode("latin-1", "replace").decode("utf-8", "replace")
        parsed = urlparse(raw)
        route = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        if route == "/healthz":
            self._send("ok", ctype="text/plain; charset=utf-8")
            return

        try:
            conn = _connect(self.db_path)
        except sqlite3.OperationalError:
            self._send(
                webui.page("Дані ще збираються",
                           webui.empty("База ще порожня",
                                       "Перший обхід каталогу ще не завершився.")),
                status=503)
            return

        try:
            query = (params.get("q") or [""])[0].strip()
            category = (params.get("cat") or [""])[0].strip()
            sort = (params.get("sort") or [""])[0].strip()
            args = (query, category, sort)
            if route in ("/", "/search"):
                self._send(self.dashboard.home(conn, *args))
            elif route == "/cross-store":
                self._send(self.dashboard.cross_store(conn, *args))
            elif route == "/fakes":
                self._send(self.dashboard.fakes(conn, *args))
            elif route == "/index":
                self._send(self.dashboard.index(conn, *args))
            elif route == "/product":
                branch = (params.get("b") or [""])[0]
                product = (params.get("p") or [""])[0]
                html = self.dashboard.product(conn, branch, product)
                if html is None:
                    self._send(webui.page("Не знайдено",
                                          webui.empty("Такого товару немає",
                                                      "Можливо, він зник з каталогу.")),
                               status=404)
                else:
                    self._send(html)
            elif route == "/api/overview":
                data = analytics.overview(conn, self.dashboard.labels)
                self._send(json.dumps(data, ensure_ascii=False),
                           ctype="application/json; charset=utf-8")
            else:
                self._send(webui.page("Не знайдено",
                                      webui.empty("Сторінки немає", route)), status=404)
        except Exception:
            log.exception("Помилка на %s", route)
            self._send(webui.page("Помилка",
                                  webui.empty("Щось пішло не так",
                                              "Подробиці — у логах контейнера.")),
                       status=500)


def serve(cfg: Config, stop_event: threading.Event) -> None:
    handler = type("BoundHandler", (Handler,), {
        "dashboard": Dashboard(cfg),
        "db_path": cfg.state_path,
    })
    httpd = ThreadingHTTPServer(("0.0.0.0", cfg.web_port), handler)
    httpd.daemon_threads = True
    log.info("Дашборд слухає порт %d", cfg.web_port)

    thread = threading.Thread(target=httpd.serve_forever, name="web", daemon=True)
    thread.start()
    stop_event.wait()
    httpd.shutdown()


def start_in_background(cfg: Config, stop_event: threading.Event) -> threading.Thread:
    thread = threading.Thread(target=serve, args=(cfg, stop_event), name="web-main", daemon=True)
    thread.start()
    return thread
