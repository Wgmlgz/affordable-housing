#!/usr/bin/env python3
"""Small local archive for Cian listing pages.

Run:  python app.py
Open: http://127.0.0.1:8787

The collector tries Cian's structured public offer endpoint first and then the
listing page. It does not solve or bypass CAPTCHAs.
"""
from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import shutil
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "archive"
ARCHIVE.mkdir(exist_ok=True)
MAX_IMAGES = 30
USER_AGENT = "Mozilla/5.0 (compatible; CianArchive/1.0; +local-personal-use)"
INSECURE_TLS_CONTEXT = ssl._create_unverified_context()


class CollectionError(Exception):
    pass


def clean(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", html.unescape(text)).strip() or None


def fetch(url: str, binary: bool = False, timeout: int = 30) -> tuple[bytes, str]:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=INSECURE_TLS_CONTEXT) as response:
            kind = response.headers.get_content_type()
            data = response.read(20_000_000 if binary else 8_000_000)
            return data, kind
    except urllib.error.HTTPError as error:
        raise CollectionError(f"Cian returned HTTP {error.code}.") from error
    except urllib.error.URLError as error:
        raise CollectionError(f"Could not reach Cian: {error.reason}") from error


def fetch_api_listing(listing_id: str) -> dict:
    """Use Cian's structured offer endpoint before attempting HTML parsing."""
    payload = json.dumps({
        "cianOfferIds": [int(listing_id)],
        "jsonQuery": {"_type": "flatsale", "engine_version": {"type": "term", "value": 2}},
    }).encode()
    request = urllib.request.Request(
        "https://api.cian.ru/search-offers/v1/get-offers-by-ids-desktop/", payload,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Origin": "https://www.cian.ru", "Referer": "https://www.cian.ru/"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=INSECURE_TLS_CONTEXT) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        raise CollectionError("structured API unavailable") from error
    offers = decoded.get("offersSerialized", [])
    if not offers:
        raise CollectionError("structured API returned no offer")
    offer = offers[0]
    if isinstance(offer, str):
        offer = json.loads(offer)
    if not isinstance(offer, dict):
        raise CollectionError("structured API returned an invalid offer")
    return offer


def values(value, wanted: set[str]):
    """Find scalar values in an unknown API payload without coupling to its schema."""
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in wanted and isinstance(item, (str, int, float)):
                result.append(str(item))
            result.extend(values(item, wanted))
    elif isinstance(value, list):
        for item in value: result.extend(values(item, wanted))
    return result


def meta(page: str, name: str) -> str | None:
    patterns = [
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
    ]
    for pattern in patterns:
        found = re.search(pattern, page, re.I)
        if found:
            return clean(found.group(1))
    return None


def json_ld(page: str) -> list[dict]:
    result = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        try:
            parsed = json.loads(raw.strip())
            result.extend(parsed if isinstance(parsed, list) else [parsed])
        except json.JSONDecodeError:
            pass
    return [x for x in result if isinstance(x, dict)]


def image_urls(page: str, ld: list[dict]) -> list[str]:
    found: list[str] = []
    for item in ld:
        value = item.get("image")
        if isinstance(value, str): found.append(value)
        if isinstance(value, list): found.extend(x for x in value if isinstance(x, str))
    # Cian's image CDN URLs are normally in either embedded state or img tags.
    found.extend(re.findall(r'https?:\\?/\\?/[^"\'<> ]+(?:\\?/)?[^"]*?(?:\.jpg|\.jpeg|\.webp)(?:\?[^"\'<> ]*)?', page, re.I))
    unique = []
    for url in found:
        url = html.unescape(url).replace("\\/", "/")
        if url.startswith("//"): url = "https:" + url
        if url.startswith("https://") and url not in unique:
            unique.append(url)
    return unique[:MAX_IMAGES]


def pasted_url(value: str) -> str:
    """Extract a URL when a browser or chat app pasted a formatted link."""
    value = html.unescape(value).strip()
    # Some apps copy a link as Markdown: [label](https://example.test/path).
    markdown = re.fullmatch(r"\[[^\]]*\]\((https?://[^\s)]+)\)", value)
    if markdown:
        return markdown.group(1)
    # Also accept an angle-bracketed URL or a URL surrounded by explanatory text.
    found = re.search(r"https?://[^\s<>\[\]()]+", value)
    return found.group(0).rstrip(".,;:!?") if found else value


def cian_valuation_report_url(offer: dict, listing_id: str) -> str | None:
    """Build Cian's public valuation-report URL from a structured flat offer."""
    geo = offer.get("geo") if isinstance(offer.get("geo"), dict) else {}
    address = geo.get("userInput")
    if isinstance(address, str):
        address = re.sub(r"^Россия,\s*", "", address, flags=re.I)
    params = {
        "address": address,
        "totalArea": offer.get("totalArea"),
        "roomsCount": offer.get("roomsCount"),
        "offerId": listing_id,
        # Cian's calculator uses this value for an ordinary, non-ground floor.
        "floor": "floorOther" if offer.get("floorNumber") else None,
        "repairType": {
            "euro": "repairTypeEuro",
            "cosmetic": "repairTypeCosmetic",
            "design": "repairTypeDesign",
            "without": "repairTypeNo",
        }.get(offer.get("repairType")),
    }
    required = ("address", "totalArea", "roomsCount")
    if not all(params.get(name) is not None for name in required):
        return None
    return "https://www.cian.ru/kalkulator-nedvizhimosti/?" + urllib.parse.urlencode(
        {name: value for name, value in params.items() if value is not None}
    )


def rub_amount(value: str) -> int | None:
    """Turn Cian's compact Russian money text (for example, 28,2 млн ₽) into rubles."""
    normalized = value.replace("\xa0", " ").replace(" ", "").replace(",", ".")
    try:
        amount = float(normalized)
    except ValueError:
        return None
    return round(amount * 1_000_000) if amount > 0 else None


def cian_valuation_from_page(page: str) -> int | None:
    """Extract the calculator's estimate from public HTML or embedded page data."""
    for pattern in (
        r'"(?:cianEstimate|estimatedPrice|estimatePrice|valuationPrice)"\s*:\s*"?(\d{6,})',
        r'(\d+(?:[,.]\d+)?)\s*(?:млн|миллион\w*)\s*₽?\s*(?:</[^>]+>\s*){0,4}Оценка\s+Циана',
        r'Оценка\s+Циана(?:</[^>]+>|\s)*[^\d]{0,120}(\d+(?:[,.]\d+)?)\s*(?:млн|миллион\w*)',
    ):
        matched = re.search(pattern, page, re.I | re.S)
        if not matched:
            continue
        value = matched.group(1)
        return int(value) if value.isdigit() and len(value) >= 6 else rub_amount(value)
    return None


def fetch_cian_valuation(offer: dict, listing_id: str) -> dict | None:
    report_url = cian_valuation_report_url(offer, listing_id)
    if not report_url:
        return None
    valuation = {"report_url": report_url}
    warning = None
    try:
        page, kind = fetch(report_url, timeout=15)
        if "html" in kind:
            estimated_price = cian_valuation_from_page(page.decode("utf-8", "replace"))
            if estimated_price:
                valuation["estimated_price_rub"] = estimated_price
            else:
                warning = "Cian's calculator did not expose an estimate in its public HTML."
        else:
            warning = "Cian's calculator did not return an HTML report."
    except CollectionError as error:
        warning = str(error)
    if "estimated_price_rub" not in valuation and warning:
        valuation["warning"] = warning
    return valuation


def form_fields(content_type: str, body: bytes) -> dict[str, list[str]]:
    """Read either browser FormData or URL-encoded request fields."""
    if content_type.lower().startswith("multipart/form-data"):
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
        )
        fields: dict[str, list[str]] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if part.get_content_disposition() == "form-data" and name:
                fields.setdefault(name, []).append(part.get_content())
        return fields
    return urllib.parse.parse_qs(body.decode("utf-8", "replace"))


def collect(url: str) -> dict:
    url = pasted_url(url)
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not (hostname == "cian.ru" or hostname.endswith(".cian.ru")):
        raise CollectionError("Please provide a http(s) URL on cian.ru.")
    listing_id = re.search(r"/(?:sale|rent)/flat/(\d+)", parsed.path)
    if not listing_id:
        raise CollectionError("This does not look like a Cian flat listing URL.")
    listing_id = listing_id.group(1)
    api_error = None
    try:
        offer = fetch_api_listing(listing_id)
        page, ld = "", []
        rooms, area = offer.get("roomsCount"), offer.get("totalArea")
        title = f"{rooms}-комн. квартира" if rooms else "Квартира"
        if area: title += f", {area} м²"
        description = offer.get("description")
        price = offer.get("bargainTerms", {}).get("priceRur")
        address_parts = offer.get("geo", {}).get("address", [])
        address = ", ".join(str(part.get("title") or part.get("name")) for part in address_parts if isinstance(part, dict) and (part.get("title") or part.get("name"))) or None
        method, raw_offer = "Cian structured offer API", offer
        urls = [photo["fullUrl"] for photo in offer.get("photos", []) if isinstance(photo, dict) and isinstance(photo.get("fullUrl"), str)]
        valuation = fetch_cian_valuation(offer, listing_id)
    except CollectionError as error:
        api_error = str(error)
        page_bytes, content_type = fetch(url)
        page = page_bytes.decode("utf-8", "replace")
        if "captcha" in page.lower() or "капча" in page.lower():
            raise CollectionError("Cian rejected both its structured API and listing page from this network; no archive was created.")
        if "html" not in content_type or len(page) < 500:
            raise CollectionError("Cian did not return a listing page.")
        ld = json_ld(page)
        primary = next((x for x in ld if x.get("@type") in ("Product", "Apartment", "Residence", "Offer")), {})
        title = meta(page, "og:title") or clean(primary.get("name")) or clean(re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S).group(1) if re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S) else None)
        description = meta(page, "description") or meta(page, "og:description") or clean(primary.get("description"))
        price = None
        offers = primary.get("offers") if isinstance(primary, dict) else None
        if isinstance(offers, dict): price = offers.get("price")
        if price is None:
            price_match = re.search(r'"price"\s*:\s*"?(\d{4,})', page)
            price = price_match.group(1) if price_match else None
        address, method, raw_offer = meta(page, "og:address"), "public HTML metadata + JSON-LD", None
        urls = image_urls(page, ld)
        valuation = None
    data = {
        "id": listing_id, "url": url, "collected_at": datetime.now(timezone.utc).isoformat(),
        "title": title, "description": description, "price_rub": price,
        "address": address, "images": [],
        "collector": {"method": method, "warnings": []},
    }
    coordinates = (raw_offer or {}).get("geo", {}).get("coordinates", {}) if isinstance(raw_offer, dict) else {}
    if coordinates.get("lat") is not None and coordinates.get("lng") is not None:
        data["location"] = {"lat": coordinates["lat"], "lng": coordinates["lng"], "source": "Cian structured offer"}
    if raw_offer is not None: data["raw_offer"] = raw_offer
    if valuation is not None: data["cian_valuation"] = valuation
    if api_error: data["collector"]["warnings"].append(api_error)
    if not title and not description:
        data["collector"]["warnings"].append("Page was reachable but exposed little public metadata.")
    target = ARCHIVE / listing_id
    if target.exists():
        shutil.rmtree(target)
    images_dir = target / "images"
    images_dir.mkdir(parents=True)

    def download(index_url):
        index, image_url = index_url
        try:
            body, kind = fetch(image_url, binary=True)
            if not kind.startswith("image/"):
                return None
            extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(kind, ".img")
            name = f"{index + 1:02d}{extension}"
            (images_dir / name).write_bytes(body)
            return {"file": f"images/{name}", "source_url": image_url, "content_type": kind}
        except (CollectionError, OSError):
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        for item in as_completed([pool.submit(download, pair) for pair in enumerate(urls)]):
            saved = item.result()
            if saved: data["images"].append(saved)
    data["images"].sort(key=lambda x: x["file"])
    (target / "listing.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def listings() -> list[dict]:
    result = []
    for file in ARCHIVE.glob("*/listing.json"):
        try: result.append(json.loads(file.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError): pass
    return sorted(result, key=lambda x: x.get("collected_at", ""), reverse=True)


def listing_heading(item: dict) -> str:
    title = str(item.get("title") or "Квартира")
    address = str(item.get("address") or "").strip()
    return f"{title} — {address}" if address and address not in title else title


def research_html(listing_id: str) -> str:
    path = ARCHIVE / listing_id / "research.md"
    if not path.is_file():
        return '<p>Исследование для этой квартиры пока не сохранено.</p>'
    source = path.read_text(encoding="utf-8")
    source = source.replace(str(ROOT) + "/archive/", "/archive/")
    source = source.replace(str(ROOT) + "/README.md", "/methodology")
    rendered = MarkdownIt("default", {"html": False}).enable("table").render(source)
    return rendered.replace("<table>", '<div class="table-scroll"><table>').replace("</table>", "</table></div>")


def assessment_rows(source: str) -> list[dict[str, str]]:
    section = re.search(r"^## 10\. Оценка\s*\n(.*?)(?=^## |\Z)", source, re.M | re.S)
    if not section:
        return []
    result = []
    for line in section.group(1).splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] == "Параметр" or re.fullmatch(r"[:\- ]+", cells[0]):
            continue
        cells = [re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", cell).replace("**", "") for cell in cells]
        result.append(dict(zip(("parameter", "score", "weight", "basis"), cells)))
    return result


def map_data() -> list[dict]:
    result = []
    for item in listings():
        location = item.get("location") or (item.get("raw_offer") or {}).get("geo", {}).get("coordinates") or {}
        if location.get("lat") is None or location.get("lng") is None:
            continue
        ident = str(item["id"])
        research = ARCHIVE / ident / "research.md"
        summary, score, assessment = "", None, []
        if research.is_file():
            content = research.read_text(encoding="utf-8")
            assessment = assessment_rows(content)
            section = re.search(r"## Вывод\s*(.*?)(?=\n## |\Z)", content, re.S)
            if section:
                conclusion = re.split(r"\s+\[(?:Объявление|Источник)\]\(", section.group(1), maxsplit=1)[0]
                summary = re.sub(r"!?\[([^]]+)\]\([^)]+\)", r"\1", conclusion)
                summary = re.sub(r"[*_#]", "", summary)
                summary = re.sub(r"^\s*Оценка\s*[—–-]\s*\d+(?:[,.]\d+)?/10\.\s*", "", summary)
                summary = re.sub(r"\s+", " ", summary).strip()[:420]
            found = re.search(r"(?:Оценка|Итог)\s*[—–-]\s*(\d+(?:[,.]\d+)?)\s*/\s*10", content)
            if found:
                score = found.group(1)
        images = item.get("images") or []
        result.append({
            "id": ident, "title": item.get("title") or "Квартира", "address": item.get("address") or "",
            "price_rub": item.get("price_rub"), "lat": float(location["lat"]), "lng": float(location["lng"]),
            "image": images[0].get("file") if images else None, "score": score, "summary": summary,
            "assessment": assessment,
            "url": item.get("url"), "has_research": research.is_file(),
        })
    return result


def flatten(value, prefix="") -> list[tuple[str, str]]:
    """Turn the saved API structure into readable leaf parameters."""
    rows = []
    if isinstance(value, dict):
        for key, item in value.items(): rows.extend(flatten(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        if all(isinstance(item, (str, int, float, bool)) or item is None for item in value):
            rows.append((prefix, ", ".join(str(item) for item in value)))
        else:
            for index, item in enumerate(value): rows.extend(flatten(item, f"{prefix}[{index}]"))
    elif value is not None:
        rows.append((prefix, str(value)))
    return rows


def listing_page(listing_id: str) -> str | None:
    file = ARCHIVE / listing_id / "listing.json"
    if not file.is_file(): return None
    item = json.loads(file.read_text(encoding="utf-8"))
    heading = html.escape(listing_heading(item))
    escaped_id = html.escape(listing_id)
    valuation = item.get("cian_valuation") if isinstance(item.get("cian_valuation"), dict) else {}
    fields = [("ID", item.get("id")), ("Цена", f'{item.get("price_rub")} ₽' if item.get("price_rub") else None), ("Оценка Циана", f'{valuation.get("estimated_price_rub")} ₽' if valuation.get("estimated_price_rub") else None), ("Адрес", item.get("address")), ("Собрано", item.get("collected_at")), ("Источник", item.get("url"))]
    facts = "".join(f"<dt>{html.escape(label)}</dt><dd>{html.escape(str(value))}</dd>" for label, value in fields if value)
    photos = "".join(f'<a href="/archive/{escaped_id}/{html.escape(photo["file"])}" target="_blank"><img src="/archive/{escaped_id}/{html.escape(photo["file"])}" alt="Фото объявления"></a>' for photo in item.get("images", []))
    raw = item.get("raw_offer", item)
    params = "".join(f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in flatten(raw))
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>{heading}</title><style>
*{{box-sizing:border-box}}body{{max-width:1000px;margin:32px auto;padding:0 16px;font:16px Arial;color:#111;background:#fff}}a{{color:#111}}h1{{margin-bottom:6px}}dl{{display:grid;grid-template-columns:150px 1fr;border-top:1px solid #111;margin:24px 0}}dt,dd{{margin:0;padding:10px;border-bottom:1px solid #ddd}}dt{{font-weight:bold}}.description{{white-space:pre-wrap;line-height:1.45;max-width:760px}}.gallery{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin:24px 0}}.gallery img{{width:100%;height:140px;object-fit:cover;display:block}}details{{margin:28px 0}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{text-align:left;vertical-align:top;padding:7px;border:1px solid #bbb;overflow-wrap:anywhere}}th{{background:#f3f3f3}}.api-params th{{width:36%}}.research{{border-top:2px solid #111;margin:36px 0;padding-top:8px;line-height:1.55;overflow-wrap:anywhere}}.research h1{{display:none}}.research h2{{margin-top:32px}}.research .table-scroll{{max-width:100%;overflow-x:auto;margin:16px 0}}.research .table-scroll table{{min-width:760px;width:100%;margin:0;font-size:14px}}.research .table-scroll th,.research .table-scroll td{{width:auto;overflow-wrap:normal;word-break:normal;line-height:1.4}}.research img{{max-width:100%;height:auto}}.research blockquote{{border-left:3px solid #bbb;margin-left:0;padding-left:16px;color:#444}}.research code{{white-space:pre-wrap}}@media(max-width:600px){{dl{{grid-template-columns:110px 1fr}}.research table{{font-size:12px}}}}</style>
<a href="/">← Список</a> · <a href="/map?selected={escaped_id}">На карте</a><h1>{heading}</h1><a href="{html.escape(item["url"])}" target="_blank">Объявление Циан</a>{f' · <a href="{html.escape(valuation["report_url"])}" target="_blank">Оценка Циана</a>' if valuation.get("report_url") else ''} · <a href="/archive/{escaped_id}/listing.json" target="_blank">JSON</a> · <a href="#research">Исследование ↓</a><dl>{facts}</dl>{f'<p>{html.escape(valuation["warning"])}</p>' if valuation.get("warning") else ''}{f'<h2>Описание</h2><p class="description">{html.escape(item["description"])}</p>' if item.get("description") else ''}<h2>Фото ({len(item.get("images", []))})</h2><div class="gallery">{photos or '<p>Нет сохранённых фото.</p>'}</div><section id="research" class="research">{research_html(listing_id)}</section><details class="api-params"><summary>Все параметры API ({len(flatten(raw))})</summary><table><thead><tr><th>Параметр</th><th>Значение</th></tr></thead><tbody>{params}</tbody></table></details></html>'''


def comparison_data() -> list[dict]:
    data = []
    for item in listings():
        flat = dict(flatten(item.get("raw_offer", item)))
        # Normalized archive fields are more useful names than their API equivalents.
        valuation = item.get("cian_valuation") if isinstance(item.get("cian_valuation"), dict) else {}
        flat.update({"archive.price_rub": str(item.get("price_rub") or ""), "archive.cian_estimated_price_rub": str(valuation.get("estimated_price_rub") or ""), "archive.title": item.get("title") or "", "archive.address": item.get("address") or ""})
        data.append({"id": item["id"], "title": listing_heading(item), "url": item["url"], "image": item.get("images", [{}])[0].get("file"), "fields": flat})
    return data


def comparison_page() -> str:
    return '''<!doctype html><meta charset="utf-8"><title>Compare listings</title><style>
*{box-sizing:border-box}body{max-width:1200px;margin:32px auto;padding:0 16px;font:15px Arial;color:#111;background:#fff}a{color:#111}h1{margin-bottom:4px}.hint{color:#555;max-width:760px;line-height:1.4}section{border-top:1px solid #111;padding:16px 0;margin-top:20px}.row{display:grid;grid-template-columns:minmax(210px,1fr) 100px 120px 44px;gap:8px;margin:8px 0}.filter{grid-template-columns:minmax(210px,1fr) 130px 130px 44px}select,input,button{font:inherit;border:1px solid #111;background:#fff;padding:8px;min-width:0}button{cursor:pointer}.controls{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}table{border-collapse:collapse;width:100%;margin-top:14px}th,td{padding:9px;border:1px solid #aaa;text-align:left;vertical-align:top}th{background:#f3f3f3;position:sticky;top:0}.score{font-weight:bold;font-size:17px}.muted{color:#555}.cardimg{width:96px;height:64px;object-fit:cover;background:#eee}@media(max-width:650px){.row,.filter{grid-template-columns:1fr 90px 90px 38px}table{font-size:12px}}</style>
<a href="/">← Archive</a><h1>Compare listings</h1><p class="hint">Filter candidates, then choose any numeric Cian parameter and its importance. Scores are normalized only among the remaining listings. Settings are saved in this browser.</p>
<section><h2>Filters</h2><p class="muted">Keep a bound blank to ignore it. Both bounds are inclusive.</p><div id="filters"></div><button id="addFilter">+ Numeric filter</button></section>
<section><h2>Score weights</h2><p class="muted">Higher means better unless you select “Lower is better”. A weight of 0 has no effect.</p><div id="metrics"></div><button id="addMetric">+ Score parameter</button><div class="controls"><button id="calculate">Update comparison</button><button id="reset">Reset settings</button></div></section>
<section><h2>Results <span id="count"></span></h2><div id="results"></div></section>
<script>
let data=[], numeric=[];const $=s=>document.querySelector(s), key='cian-compare-v1';
function opts(){return numeric.map(x=>`<option value="${x}">${x}</option>`).join('')}
function row(type, saved={}){let el=document.createElement('div');el.className='row '+type;el.innerHTML=type==='filter'?`<select>${opts()}</select><input type="number" step="any" placeholder="min"><input type="number" step="any" placeholder="max"><button title="Remove">×</button>`:`<select>${opts()}</select><input type="number" step="any" value="${saved.weight??1}" placeholder="weight"><select><option value="high">Higher is better</option><option value="low">Lower is better</option></select><button title="Remove">×</button>`;let s=el.querySelector('select');s.value=saved.field||numeric[0];if(type==='filter'){let i=el.querySelectorAll('input');i[0].value=saved.min??'';i[1].value=saved.max??''}else el.querySelectorAll('select')[1].value=saved.direction||'high';el.querySelector('button').onclick=()=>el.remove();$('#'+(type==='filter'?'filters':'metrics')).append(el)}
function state(){return {filters:[...$('#filters').children].map(x=>{let i=x.querySelectorAll('input');return {field:x.querySelector('select').value,min:i[0].value,max:i[1].value}}),metrics:[...$('#metrics').children].map(x=>{let s=x.querySelectorAll('select');return {field:s[0].value,weight:x.querySelector('input').value,direction:s[1].value}})}}
function num(item,field){let v=parseFloat(item.fields[field]);return Number.isFinite(v)?v:null}
function calculate(){let st=state();localStorage.setItem(key,JSON.stringify(st));let rows=data.filter(item=>st.filters.every(f=>{let v=num(item,f.field);return v!==null&&(f.min===''||v>=+f.min)&&(f.max===''||v<=+f.max)}));let active=st.metrics.filter(m=>+m.weight!==0);for(let item of rows){item.score=0;item.parts=[]}for(let m of active){let vals=rows.map(x=>num(x,m.field)).filter(x=>x!==null);let lo=Math.min(...vals),hi=Math.max(...vals);for(let item of rows){let v=num(item,m.field), n=v===null?0.5:(hi===lo?0.5:(v-lo)/(hi-lo));if(m.direction==='low')n=1-n;item.score+=n*(+m.weight);item.parts.push(`${m.field}: ${v??'—'} × ${m.weight}`)}}rows.sort((a,b)=>b.score-a.score);$('#count').textContent=`(${rows.length} of ${data.length})`;let head=active.map(m=>`<th>${m.field}</th>`).join('');$('#results').innerHTML=rows.length?`<table><thead><tr><th>Listing</th><th>Score</th>${head}<th>Why</th></tr></thead><tbody>${rows.map(x=>`<tr><td>${x.image?`<img class="cardimg" src="/archive/${x.id}/${x.image}">`:''}<br><a href="/listing/${x.id}">${x.title}</a></td><td class="score">${x.score.toFixed(2)}</td>${active.map(m=>`<td>${x.fields[m.field]??'—'}</td>`).join('')}<td class="muted">${x.parts.join('<br>')}</td></tr>`).join('')}</tbody></table>`:'<p>No listings match the filters.</p>'}
async function init(){data=await (await fetch('/api/comparison-data')).json();let fields=new Set;data.forEach(x=>Object.entries(x.fields).forEach(([k,v])=>{if(v!==''&&Number.isFinite(parseFloat(v))&&String(parseFloat(v))===String(v).trim())fields.add(k)}));numeric=[...fields].sort();let saved;try{saved=JSON.parse(localStorage.getItem(key))}catch{};(saved?.filters||[]).forEach(x=>row('filter',x));(saved?.metrics||[{field:'archive.price_rub',weight:1,direction:'low'}]).forEach(x=>row('metric',x));calculate()};$('#addFilter').onclick=()=>{row('filter');calculate()};$('#addMetric').onclick=()=>{row('metric');calculate()};$('#filters').addEventListener('input',calculate);$('#filters').addEventListener('change',calculate);$('#metrics').addEventListener('input',calculate);$('#metrics').addEventListener('change',calculate);$('#calculate').onclick=calculate;$('#reset').onclick=()=>{localStorage.removeItem(key);location.reload()};init();</script>'''


def map_page() -> str:
    return '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Карта квартир</title><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""><style>
*{box-sizing:border-box}body{margin:0;font:16px Arial,sans-serif;color:#171717;background:#fff}header{padding:13px 20px;display:flex;align-items:center;gap:16px;border-bottom:1px solid #ddd}a{color:#171717}header h1{font-size:19px;margin:0}.layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(480px,44vw);height:calc(100vh - 54px)}#map{min-height:400px}.card{padding:20px;overflow:auto;border-left:1px solid #ddd}.card-top{display:flex;gap:12px;align-items:flex-start}.card-top img{width:112px;height:94px;flex:none;object-fit:cover;border-radius:5px;background:#eee}.card h2{font-size:18px;line-height:1.25;margin:0 0 7px}.card h3{font-size:17px;margin:22px 0 8px}.address{color:#555;line-height:1.4}.price{font-size:21px;font-weight:bold;margin:12px 0}.score{display:inline-block;background:#e8f5ed;border-radius:4px;padding:6px 9px}.summary{line-height:1.5;color:#333;margin:0}.assessment{width:100%;border-collapse:collapse;table-layout:fixed;font-size:12px;line-height:1.35}.assessment th,.assessment td{border:1px solid #ccc;padding:6px;text-align:left;vertical-align:top;overflow-wrap:break-word}.assessment th{background:#f4f4f4}.assessment th:first-child{width:115px}.assessment th:nth-child(2){width:64px}.assessment th:nth-child(3){width:42px}.assessment td:nth-child(2),.assessment td:nth-child(3){text-align:right;white-space:nowrap}.links{display:flex;gap:14px;flex-wrap:wrap;margin-top:20px}.muted{color:#777}.leaflet-container{font:inherit}@media(max-width:750px){.layout{display:flex;flex-direction:column;height:auto}#map{height:52vh;min-height:350px}.card{border-left:0;border-top:1px solid #ddd;min-height:320px}}
</style></head><body><header><a href="/">← Список</a><h1>Карта квартир</h1></header><main class="layout"><div id="map" role="application" aria-label="Карта активных квартир"></div><aside class="card" id="card"><p class="muted">Выберите квартиру на карте.</p></aside></main><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script><script>
const card=document.querySelector('#card');
function add(tag,text,parent=card,className=''){const el=document.createElement(tag);el.textContent=text; if(className)el.className=className;parent.append(el);return el}
function link(text,url,parent=card){const a=add('a',text,parent);a.href=url;return a}
function show(item,marker){
  card.replaceChildren();
  const top=add('div','',card,'card-top');
  if(item.image){const img=document.createElement('img');img.src='/archive/'+encodeURIComponent(item.id)+'/'+item.image.split('/').map(encodeURIComponent).join('/');img.alt='Фото квартиры';top.append(img)}
  const info=add('div','',top);add('h2',item.title,info);add('p',item.address,info,'address');
  if(item.price_rub)add('p',new Intl.NumberFormat('ru-RU').format(item.price_rub)+' ₽',card,'price');
  if(item.score)add('span','Оценка: '+item.score+'/10',card,'score');
  if(item.summary){add('h3','Вывод');add('p',item.summary,card,'summary')}
  if(item.assessment?.length){
    add('h3','10. Оценка');
    const table=document.createElement('table');table.className='assessment';card.append(table);
    const head=document.createElement('thead');const headers=document.createElement('tr');head.append(headers);table.append(head);
    for(const label of ['Параметр','Балл /10','Вес','Основание'])add('th',label,headers);
    const body=document.createElement('tbody');table.append(body);
    for(const row of item.assessment){const tr=document.createElement('tr');body.append(tr);for(const key of ['parameter','score','weight','basis'])add('td',row[key],tr)}
  }
  const links=add('div','',card,'links');link('Открыть квартиру и исследование →','/listing/'+encodeURIComponent(item.id),links);if(item.has_research)link('К исследованию','/listing/'+encodeURIComponent(item.id)+'#research',links);if(item.url)link('Циан ↗',item.url,links);
  if(marker)marker.setStyle({fillColor:'#d33',color:'#a22'});
  history.replaceState(null,'','/map?selected='+encodeURIComponent(item.id));
}
async function init(){let data;try{const response=await fetch('/api/map-data');if(!response.ok)throw Error('HTTP '+response.status);data=await response.json()}catch(error){card.textContent='Не удалось загрузить квартиры: '+error.message;return}if(!window.L){card.textContent='Не удалось загрузить карту. Проверьте подключение к интернету.';return}const map=L.map('map').setView([55.8,37.52],12);L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);const points=[];const markers=new Map();for(const item of data){const marker=L.circleMarker([item.lat,item.lng],{radius:9,color:'#205a84',weight:2,fillColor:'#2886bd',fillOpacity:.9}).addTo(map);marker.bindTooltip(item.address||item.title);marker.on('click',()=>{for(const m of markers.values())m.setStyle({fillColor:'#2886bd',color:'#205a84'});show(item,marker)});markers.set(item.id,marker);points.push([item.lat,item.lng])}if(points.length)map.fitBounds(points,{padding:[35,35],maxZoom:13});else card.textContent='Нет квартир с координатами.';const selected=new URLSearchParams(location.search).get('selected');const initial=data.find(x=>x.id===selected)||data[0];if(initial){show(initial,markers.get(initial.id));if(selected)map.panTo([initial.lat,initial.lng])}}
init();
</script></body></html>'''


def page() -> str:
    cards = []
    for item in listings():
        ident = html.escape(str(item["id"]))
        image = item.get("images", [{}])[0].get("file") if item.get("images") else None
        preview = f'<img src="/archive/{ident}/{html.escape(image)}" alt="" />' if image else '<div class="empty">no image</div>'
        cards.append(f'<article>{preview}<div><h2>{html.escape(listing_heading(item))}</h2><p>{html.escape(str(item.get("price_rub") or "Price unavailable"))} ₽</p><a href="/listing/{ident}">View details</a> · <a href="/archive/{ident}/listing.json" target="_blank">JSON</a> · <a href="{html.escape(item["url"])}" target="_blank">Cian</a> <button data-id="{ident}">Delete</button></div></article>')
    return f'''<!doctype html><meta charset="utf-8"><title>Cian archive</title><style>
*{{box-sizing:border-box}} body{{max-width:840px;margin:40px auto;padding:0 16px;font:16px Arial;color:#111;background:#fff}} input,button{{font:inherit;border:1px solid #111;background:#fff;padding:10px}} input{{width:min(580px,100%)}} button{{cursor:pointer}} form{{display:flex;gap:8px;flex-wrap:wrap}} #status{{min-height:24px;margin:14px 0}} article{{border-top:1px solid #111;padding:16px 0;display:flex;gap:16px}} img,.empty{{width:150px;height:100px;object-fit:cover;background:#eee;flex:none}} .empty{{display:grid;place-items:center;color:#555}} h2{{font-size:17px;margin:0 0 8px}} p{{margin:0 0 8px}} a{{color:#111}} </style>
<h1>Cian archive</h1><p><a href="/map">Карта квартир →</a> · <a href="/compare">Сравнить объявления →</a></p><form id="import"><input name="url" required placeholder="https://www.cian.ru/sale/flat/332630276"><button>Collect</button></form><div id="status"></div><section>{''.join(cards) or '<p>No saved listings yet.</p>'}</section>
<script>const s=document.querySelector('#status');document.querySelector('#import').onsubmit=async e=>{{e.preventDefault();s.textContent='Collecting…';let r=await fetch('/api/collect',{{method:'POST',body:new FormData(e.target)}});let x=await r.json();s.textContent=x.message||x.error; if(r.ok)setTimeout(()=>location.reload(),500)}};document.querySelectorAll('button[data-id]').forEach(b=>b.onclick=async()=>{{if(confirm('Delete this archive?')){{await fetch('/api/listings/'+b.dataset.id,{{method:'DELETE'}});location.reload()}}}})</script>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): print(fmt % args)
    def send(self, status, body, kind="text/html; charset=utf-8"):
        raw = body.encode() if isinstance(body, str) else body
        self.send_response(status); self.send_header("Content-Type", kind); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if route == "/": return self.send(200, page())
        if route == "/compare": return self.send(200, comparison_page())
        if route == "/map": return self.send(200, map_page())
        if route == "/methodology": return self.send(200, MarkdownIt("default", {"html": False}).enable("table").render((ROOT / "README.md").read_text(encoding="utf-8")))
        if route == "/api/comparison-data": return self.send(200, json.dumps(comparison_data(), ensure_ascii=False), "application/json")
        if route == "/api/map-data": return self.send(200, json.dumps(map_data(), ensure_ascii=False), "application/json")
        matched = re.fullmatch(r"/listing/(\d+)", route)
        if matched:
            content = listing_page(matched.group(1))
            return self.send(200, content) if content else self.send_error(404)
        path = (ROOT / urllib.parse.unquote(route.lstrip("/"))).resolve()
        if str(path).startswith(str(ARCHIVE.resolve())) and path.is_file():
            kind = "application/json" if path.suffix == ".json" else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            return self.send(200, path.read_bytes(), kind)
        self.send_error(404)
    def do_POST(self):
        if self.path != "/api/collect": return self.send_error(404)
        length = int(self.headers.get("Content-Length", "0"))
        fields = form_fields(self.headers.get("Content-Type", ""), self.rfile.read(length))
        try:
            item = collect(fields.get("url", [""])[0]); return self.send(200, json.dumps({"message": f"Saved {item['id']} with {len(item['images'])} images."}), "application/json")
        except CollectionError as error: return self.send(422, json.dumps({"error": str(error)}), "application/json")
    def do_DELETE(self):
        matched = re.fullmatch(r"/api/listings/(\d+)", self.path)
        if not matched: return self.send_error(404)
        target = (ARCHIVE / matched.group(1)).resolve()
        if target.parent == ARCHIVE.resolve() and target.exists(): shutil.rmtree(target)
        self.send(204, b"")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "8787"))), Handler)
    print("Cian archive: http://127.0.0.1:8787")
    server.serve_forever()
