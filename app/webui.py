"""HTML для дашборду. Жодних зовнішніх скриптів — усе інлайном."""
from __future__ import annotations

from datetime import datetime
from html import escape as e

from .formatting import money

GROUP_TITLES = {
    "strong": "Міцний алкоголь",
    "beer": "Пиво",
    "cider": "Слабоалкогольні, сидр",
    "vermouth": "Вермути",
    "wine_still": "Тихі вина",
    "wine_sparkling": "Ігристі",
    "nonalco": "Безалкогольне",
}

GROUP_COLORS = {
    "strong": "#B8861F",
    "beer": "#C97B2E",
    "cider": "#7A9A3B",
    "vermouth": "#8E5A9E",
    "wine_still": "#A33B4E",
    "wine_sparkling": "#4E8FA3",
    "nonalco": "#6B7280",
}

CSS = """
/* Mobile-first: базові стилі — для телефона, десктоп добудовується медіа-запитом. */
:root{
  --ground:#F6F4EF;--surface:#FFFDF8;--sunk:#EDEAE1;--ink:#17150F;--soft:#4A463B;
  --faint:#7C7566;--rule:#DCD7CA;--accent:#B8861F;--accent-soft:#F2E4C2;
  --bad:#A32B22;--bad-soft:#F6E2DF;--good:#2E6B4F;--good-soft:#DDEBE3;
  --mono:"SFMono-Regular",Consolas,"Liberation Mono",monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --tap:44px;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --ground:#14130F;--surface:#1D1B15;--sunk:#262319;--ink:#F2EFE6;--soft:#BEB8A8;
  --faint:#948D7C;--rule:#35322A;--accent:#E0AC42;--accent-soft:#3A2F16;
  --bad:#E8776B;--bad-soft:#3A211E;--good:#6FC099;--good-soft:#1C2E26;
}}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html{-webkit-text-size-adjust:100%}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);
  font-size:16px;line-height:1.5;-webkit-font-smoothing:antialiased;
  overflow-x:hidden}
.wrap{padding:.9rem .9rem calc(3rem + env(safe-area-inset-bottom));
  display:flex;flex-direction:column;gap:1.4rem;max-width:1180px;margin:0 auto}
a{color:inherit;text-decoration:none}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

/* --- шапка --- */
header.top{display:flex;flex-direction:column;gap:.7rem;
  border-bottom:2px solid var(--ink);padding-bottom:.6rem}
.brand{font-size:1.3rem;font-weight:700;letter-spacing:-.02em;display:block}
.brand span{color:var(--accent)}
.updated{font-family:var(--mono);font-size:.72rem;color:var(--faint);margin-top:.1rem}
nav{display:flex;gap:.4rem;overflow-x:auto;scrollbar-width:none;
  margin:0 -.9rem;padding:0 .9rem}
nav::-webkit-scrollbar{display:none}
nav a{color:var(--soft);font-size:.85rem;white-space:nowrap;
  padding:.5rem .75rem;background:var(--surface);border:1px solid var(--rule);
  min-height:var(--tap);display:flex;align-items:center}
nav a.on{background:var(--accent);border-color:var(--accent);color:#17150F;font-weight:600}

/* --- пошук --- */
form.search{display:flex;gap:.5rem;position:sticky;top:0;z-index:5;
  background:var(--ground);padding:.5rem 0}
form.search input{flex:1;min-width:0;background:var(--surface);color:var(--ink);
  border:1px solid var(--rule);padding:0 .85rem;font-size:16px;
  font-family:var(--sans);min-height:var(--tap)}
form.search button{background:var(--accent);color:#17150F;border:0;padding:0 1.1rem;
  font-weight:600;font-size:.9rem;min-height:var(--tap);cursor:pointer}

/* --- KPI --- */
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;
  background:var(--rule);border:1px solid var(--rule)}
.kpi{background:var(--surface);padding:.7rem .8rem}
.kpi dt{font-family:var(--mono);font-size:.64rem;letter-spacing:.08em;
  text-transform:uppercase;color:var(--faint);margin-bottom:.25rem}
.kpi dd{font-family:var(--mono);font-size:1.35rem;font-weight:600;
  font-variant-numeric:tabular-nums;letter-spacing:-.02em;line-height:1.1}
.kpi dd small{font-size:.7rem;font-weight:400;color:var(--faint);letter-spacing:0}

section{display:flex;flex-direction:column;gap:.7rem}
h2{font-size:1.1rem;font-weight:700;letter-spacing:-.01em;
  display:flex;flex-direction:column;gap:.15rem}
h2 .hint{font-size:.75rem;font-weight:400;color:var(--faint)}
.more{min-height:var(--tap);display:flex;align-items:center;
  font-size:.85rem;font-weight:600;color:var(--accent)}
.lead{font-size:.85rem;color:var(--soft)}

/* --- картки товарів --- */
.cards{display:flex;flex-direction:column;gap:1px;
  background:var(--rule);border:1px solid var(--rule)}
.card{background:var(--surface);padding:.75rem;display:flex;gap:.75rem;
  align-items:flex-start;min-height:var(--tap)}
.card:active{background:var(--sunk)}
.thumb{width:60px;height:60px;flex-shrink:0;object-fit:contain;background:var(--sunk)}
.card-main{min-width:0;display:flex;flex-direction:column;gap:.3rem;flex:1}
.card-title{font-size:.92rem;font-weight:600;line-height:1.3;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-prices{font-family:var(--mono);font-variant-numeric:tabular-nums;
  display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap}
.now{font-size:1.1rem;font-weight:700}
.was{font-size:.82rem;color:var(--faint);text-decoration:line-through}
.card-meta{font-size:.74rem;color:var(--faint);display:flex;gap:.35rem;
  flex-wrap:wrap;align-items:center}

.pill{font-family:var(--mono);font-size:.7rem;font-weight:600;padding:.15rem .45rem;
  white-space:nowrap;line-height:1.4}
.pill.claim{background:var(--bad-soft);color:var(--bad)}
.pill.real{background:var(--good-soft);color:var(--good)}
.pill.warn{background:var(--bad);color:#fff}
.pill.soft{background:var(--sunk);color:var(--faint)}
.pill.store{background:var(--accent-soft);color:var(--ink)}

/* --- порівняння магазинів: картка, а не таблиця --- */
.gaps{display:flex;flex-direction:column;gap:1px;
  background:var(--rule);border:1px solid var(--rule)}
.gap{background:var(--surface);padding:.75rem;display:flex;flex-direction:column;gap:.5rem}
.gap-head{display:flex;justify-content:space-between;gap:.6rem;align-items:flex-start}
.gap-title{font-size:.92rem;font-weight:600;line-height:1.3;min-width:0;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.gap-save{font-family:var(--mono);font-size:.9rem;font-weight:700;color:var(--good);
  white-space:nowrap;text-align:right;line-height:1.2}
.gap-save small{display:block;font-size:.68rem;font-weight:500;color:var(--faint)}
.gap-rows{display:flex;flex-direction:column;gap:.25rem}
.gap-row{display:flex;justify-content:space-between;gap:.6rem;align-items:center;
  font-size:.84rem;padding:.3rem .5rem;background:var(--sunk)}
.gap-row.win{background:var(--good-soft)}
.gap-row .store{color:var(--soft);min-width:0;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.gap-row.win .store{color:var(--good);font-weight:600}
.gap-row .price{font-family:var(--mono);font-variant-numeric:tabular-nums;
  font-weight:600;white-space:nowrap}

.empty{background:var(--surface);border:1px dashed var(--rule);padding:1.1rem;
  color:var(--faint);font-size:.87rem;display:flex;flex-direction:column;gap:.4rem}
.empty b{color:var(--ink)}

.chart{background:var(--surface);border:1px solid var(--rule);padding:.7rem}
.chart svg{display:block;width:100%;height:auto}
.legend{display:flex;flex-wrap:wrap;gap:.7rem;font-size:.76rem;color:var(--soft);
  margin-top:.5rem}
.legend i{display:inline-block;width:.7rem;height:.7rem;margin-right:.3rem;
  vertical-align:-1px}

/* --- сторінка товару --- */
.detail{display:flex;flex-direction:column;gap:1rem}
.facts{background:var(--surface);border:1px solid var(--rule)}
.facts div{display:flex;justify-content:space-between;gap:1rem;padding:.6rem .8rem;
  border-bottom:1px solid var(--rule);font-size:.86rem;align-items:center}
.facts div:last-child{border-bottom:none}
.facts dt{color:var(--faint);flex-shrink:0}
.facts dd{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right}
.hero{display:flex;gap:.9rem;align-items:flex-start}
.hero img{width:88px;height:88px;flex-shrink:0;object-fit:contain;background:var(--sunk)}
h1{font-size:1.25rem;letter-spacing:-.02em;line-height:1.25}
footer{border-top:1px solid var(--rule);padding-top:.8rem;font-family:var(--mono);
  font-size:.7rem;color:var(--faint)}

/* --- десктоп --- */
@media (min-width:760px){
  .wrap{padding:1.5rem 1.2rem 4rem;gap:2rem}
  header.top{flex-direction:row;flex-wrap:wrap;align-items:baseline;
    justify-content:space-between;padding-bottom:.8rem}
  nav{margin:0;padding:0;gap:.5rem}
  nav a{background:transparent;border:0;border-bottom:2px solid transparent;
    min-height:auto;padding:.2rem 0 .3rem}
  nav a.on{background:transparent;color:var(--ink);border-bottom-color:var(--accent)}
  nav a+a{margin-left:.9rem}
  form.search{position:static}
  .kpis{grid-template-columns:repeat(auto-fit,minmax(8.5rem,1fr))}
  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(20rem,1fr))}
  .gaps{display:grid;grid-template-columns:repeat(auto-fill,minmax(22rem,1fr))}
  h2{flex-direction:row;align-items:baseline;gap:.6rem}
  .detail{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:1.5rem}
  .hero img{width:120px;height:120px}
  h1{font-size:1.5rem}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""


def page(title: str, body: str, active: str = "") -> str:
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
        for key, href, label in (
            ("home", "/", "Огляд"),
            ("gap", "/cross-store", "Де дешевше"),
            ("fake", "/fakes", "Зал ганьби"),
            ("index", "/index", "Індекс цін"),
        )
    )
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    return f"""<!doctype html><html lang="uk"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#B8861F">
<meta name="color-scheme" content="light dark">
<title>{e(title)} · alco-market</title><style>{CSS}</style></head><body>
<div class="wrap">
<header class="top">
  <div><a href="/" class="brand">alco<span>·</span>market</a>
    <div class="updated">Сільпо: Білогородка та Стоянка</div></div>
  <nav>{nav}</nav>
</header>
<form class="search" action="/search" method="get">
  <input name="q" placeholder="Знайти товар — напр. «віскі» або «Martini»" autocomplete="off">
  <button type="submit">Шукати</button>
</form>
{body}
<footer>Сторінку сформовано {stamp} · дані оновлюються щодня о 09:00</footer>
</div></body></html>"""


# ----------------------------------------------------------------- фрагменти


def kpis(data: dict) -> str:
    per_branch = "".join(
        f'<div class="kpi"><dt>{e(b["label"])}</dt>'
        f'<dd>{b["in_stock"]}<small> / {b["products"]}</small></dd></div>'
        for b in data["branches"]
    )
    return f"""<dl class="kpis">
<div class="kpi"><dt>Під наглядом</dt><dd>{data['products']}</dd></div>
<div class="kpi"><dt>Зі знижкою</dt><dd>{data['discounted']}</dd></div>
<div class="kpi"><dt>Медіанна знижка</dt><dd>{data['median_depth']:.0f}<small>%</small></dd></div>
<div class="kpi"><dt>Історія</dt><dd>{data['history_days']}<small> дн.</small></dd></div>
{per_branch}
</dl>"""


def _discount_pills(row: dict) -> str:
    pills = [f'<span class="pill claim">заявлено −{row["claimed"]:.0f}%</span>']
    if row["confident"] and row["honest"] is not None:
        pills.append(f'<span class="pill real">реально −{row["honest"]:.0f}%</span>')
    else:
        pills.append(
            f'<span class="pill soft">історія {row["observed_days"]} дн.</span>'
        )
    if row["inflated"] and row["confident"]:
        pills.append('<span class="pill warn">накрутка</span>')
    return "".join(pills)


def product_card(row: dict) -> str:
    href = f'/product?b={e(row["branch_id"])}&p={e(row["product_id"])}'
    img = (
        f'<img class="thumb" src="{e(row["image"])}" alt="" loading="lazy">'
        if row.get("image") else '<div class="thumb"></div>'
    )
    was = (
        f'<span class="was">{money(row["old_price"])} ₴</span>'
        if row.get("old_price") and row["old_price"] > row["price"] else ""
    )
    stores = "".join(
        f'<span class="pill store">{e(label)}</span>'
        for label in (row.get("branch_labels") or [row.get("branch_label", "")])
        if label
    )
    return f"""<a class="card" href="{href}">{img}
<div class="card-main">
  <div class="card-title">{e(row["title"] or "")}</div>
  <div class="card-prices"><span class="now">{money(row["price"])} ₴</span>{was}</div>
  <div class="card-meta">{_discount_pills(row)}</div>
  <div class="card-meta">{stores}
    <span>{e(row.get("display_ratio") or "")}</span>
    <span>{e(row.get("category_title") or "")}</span></div>
</div></a>"""


def gap_card(g: dict) -> str:
    """Порівняння двох магазинів. Картка, а не рядок таблиці: сім колонок
    на екрані 375 px читати неможливо."""
    href = f'/product?b={e(g["b1"])}&p={e(g["product_id"])}'
    img = (
        f'<img class="thumb" src="{e(g["image"])}" alt="" loading="lazy">'
        if g.get("image") else ""
    )
    rows = "".join(
        f'<div class="gap-row{" win" if win else ""}">'
        f'<span class="store">{e(label)}</span>'
        f'<span class="price">{money(price)} ₴</span></div>'
        for label, price, win in (
            (g["cheaper_branch"], g["cheap_price"], True),
            (g["dearer_branch"], g["dear_price"], False),
        )
    )
    return f"""<a class="gap" href="{href}">
<div class="gap-head">
  {img}
  <div class="gap-title">{e(g["title"] or "")}</div>
  <div class="gap-save">−{money(g["gap"])} ₴<small>економія {g["gap_percent"]:.0f}%</small></div>
</div>
<div class="gap-rows">{rows}</div></a>"""


def gap_list(gaps: list[dict]) -> str:
    if not gaps:
        return empty("Немає з чим порівнювати", "Потрібні дані з обох магазинів.")
    return f'<div class="gaps">{"".join(gap_card(g) for g in gaps)}</div>'


def empty(title: str, note: str) -> str:
    return f'<div class="empty"><b>{e(title)}</b><span>{e(note)}</span></div>'


# -------------------------------------------------------------------- графіки


def price_chart(series: list[dict], width: int = 380, height: int = 200) -> str:
    """Східчастий графік ціни: ціна тримається, поки не змінилась."""
    distinct = {round(float(r["price"]), 2) for r in series}
    if len(series) < 2 or len(distinct) < 2:
        return empty(
            "Графіка ще немає",
            "Ціну зафіксовано лише один раз. Лінія зʼявиться після першої зміни — "
            "заміри щодня о 09:00.")

    points = []
    for row in series:
        start = datetime.fromisoformat(row["first_seen"])
        end = datetime.fromisoformat(row["last_seen"])
        points.append((start, end, float(row["price"]), bool(row["on_promo"])))

    t0 = points[0][0].timestamp()
    t1 = max(p[1].timestamp() for p in points)
    if t1 - t0 < 3600:
        t1 = t0 + 86400
    lo = min(p[2] for p in points)
    hi = max(p[2] for p in points)
    if hi - lo < 0.01:
        lo, hi = lo * 0.95, hi * 1.05

    # Поле зліва рахуємо під найдовший підпис, інакше "3 778" обрізається.
    labels_y = [money(round(hi - (hi - lo) * f)) for f in (0, 0.5, 1)]
    pad_l = 14 + 7 * max(len(s) for s in labels_y)
    pad_r, pad_t, pad_b = 10, 12, 26
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b

    def x(ts): return pad_l + (ts - t0) / (t1 - t0) * w
    def y(price): return pad_t + (hi - price) / (hi - lo) * h

    path = []
    for start, end, price, _ in points:
        x0, x1 = x(start.timestamp()), x(end.timestamp())
        yy = y(price)
        if not path:
            path.append(f"M {x0:.1f} {yy:.1f}")
        else:
            path.append(f"L {x0:.1f} {yy:.1f}")
        path.append(f"L {max(x1, x0 + 1):.1f} {yy:.1f}")

    grid = []
    for frac, label in zip((0, 0.5, 1), labels_y):
        yy = pad_t + h * frac
        grid.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" y2="{yy:.1f}" '
            f'stroke="var(--rule)" stroke-width="1"/>'
            f'<text x="{pad_l - 6}" y="{yy + 4:.1f}" text-anchor="end" '
            f'font-size="12" fill="var(--faint)" font-family="var(--mono)">'
            f'{label}</text>'
        )

    dots = []
    for start, _end, price, promo in points:
        dots.append(
            f'<circle cx="{x(start.timestamp()):.1f}" cy="{y(price):.1f}" r="3.5" '
            f'fill="{"var(--bad)" if promo else "var(--accent)"}"/>'
        )

    first_label = points[0][0].strftime("%d.%m")
    last_label = datetime.fromtimestamp(t1).strftime("%d.%m")
    return f"""<div class="chart"><svg viewBox="0 0 {width} {height}" width="100%"
 height="{height}" role="img" aria-label="Динаміка ціни">
{''.join(grid)}
<path d="{' '.join(path)}" fill="none" stroke="var(--accent)" stroke-width="2.5"
 stroke-linejoin="round"/>
{''.join(dots)}
<text x="{pad_l}" y="{height - 7}" font-size="12" fill="var(--faint)">{first_label}</text>
<text x="{width - pad_r}" y="{height - 7}" font-size="12" fill="var(--faint)"
 text-anchor="end">{last_label}</text>
</svg>
<div class="legend"><span><i style="background:var(--accent)"></i>звичайна ціна</span>
<span><i style="background:var(--bad)"></i>акційний період</span></div></div>"""


def index_chart(data: dict, width: int = 400, height: int = 250) -> str:
    series = {k: v for k, v in data["series"].items()
              if any(p["median"] for p in v)}
    if not series:
        return empty("Індекс ще порожній", "Потрібно кілька днів замірів.")

    values = [p["median"] for pts in series.values() for p in pts if p["median"]]
    lo, hi = min(values), max(values)
    if hi - lo < 1:
        lo, hi = lo * 0.9, hi * 1.1

    labels_y = [money(round(hi - (hi - lo) * f)) for f in (0, 0.5, 1)]
    pad_l = 14 + 7 * max(len(s) for s in labels_y)
    pad_r, pad_t, pad_b = 10, 12, 28
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    n = max(1, len(data["labels"]) - 1)

    def x(i): return pad_l + i / n * w
    def y(v): return pad_t + (hi - v) / (hi - lo) * h

    grid = []
    for frac, label in zip((0, 0.5, 1), labels_y):
        yy = pad_t + h * frac
        grid.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" y2="{yy:.1f}" '
            f'stroke="var(--rule)"/>'
            f'<text x="{pad_l - 6}" y="{yy + 4:.1f}" text-anchor="end" font-size="12" '
            f'fill="var(--faint)" font-family="var(--mono)">{label}</text>'
        )

    paths, legend = [], []
    for key, pts in series.items():
        colour = GROUP_COLORS.get(key, "#888")
        segs, started = [], False
        for i, point in enumerate(pts):
            if point["median"] is None:
                started = False
                continue
            cmd = "M" if not started else "L"
            segs.append(f"{cmd} {x(i):.1f} {y(point['median']):.1f}")
            started = True
        if segs:
            paths.append(
                f'<path d="{" ".join(segs)}" fill="none" stroke="{colour}" '
                f'stroke-width="2.5" stroke-linejoin="round"/>'
            )
            legend.append(
                f'<span><i style="background:{colour}"></i>'
                f'{e(GROUP_TITLES.get(key, key))}</span>'
            )

    first = data["labels"][0][8:10] + "." + data["labels"][0][5:7]
    last = data["labels"][-1][8:10] + "." + data["labels"][-1][5:7]
    return f"""<div class="chart"><svg viewBox="0 0 {width} {height}" width="100%"
 height="{height}" role="img" aria-label="Індекс цін по категоріях">
{''.join(grid)}{''.join(paths)}
<text x="{pad_l}" y="{height - 7}" font-size="12" fill="var(--faint)">{first}</text>
<text x="{width - pad_r}" y="{height - 7}" font-size="12" fill="var(--faint)"
 text-anchor="end">{last}</text>
</svg><div class="legend">{''.join(legend)}</div></div>"""
