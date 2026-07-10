#!/usr/bin/env python3
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES_FILE = ROOT / "data" / "news_sources.json"
NEWS_FILE = ROOT / "data" / "noticias.json"
STATUS_FILE = ROOT / "data" / "news_robot_status.json"
MAX_ITEMS = 30
USER_AGENT = "ControlHorasLAP-NewsRobot/1.0 (+https://github.com/Jose-Controldehoras/control-de-horas-lap)"
COMPANY_TERMS = (
    "granada la palma",
    "la palma sca",
    "la palma s.c.a",
    "la palma s.c",
    "la palma sociedad cooperativa",
    "motril la palma",
    "ugt granada la palma",
    "comite empresa la palma",
    "comite de empresa la palma",
)
SECTOR_TERMS = (
    "manipulado",
    "envasado",
    "frutas",
    "hortalizas",
    "hortofruticola",
    "hortofrutícola",
    "campo",
    "agrario",
    "agricola",
    "agrícola",
    "agricultura",
    "almacen",
    "almacén",
    "mozo",
    "cooperativa",
    "invernadero",
    "cosecha",
    "jornal",
    "convenio del campo",
    "sector del campo",
    "sector agrario",
    "sector agricola",
    "sector agrícola",
    "sector del manipulado",
)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json_if_changed(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old != text:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8"})
    with urllib.request.urlopen(request, timeout=20) as response:
        content_type = response.headers.get("content-type", "")
        raw = response.read(1_500_000)
    return raw.decode("utf-8", errors="replace"), content_type


def fetch_json(url, params):
    query = urllib.parse.urlencode(params)
    text, _ = fetch(url + ("?" + query if query else ""))
    return json.loads(text)


def clean_text(value):
    value = html.unescape(value or "")
    value = fix_mojibake(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def fix_mojibake(value):
    if not value:
        return ""
    if any(marker in value for marker in ("Ã", "Â", "â€", "â€œ", "â€")):
        for source_encoding in ("latin1", "cp1252"):
            try:
                fixed = value.encode(source_encoding, errors="ignore").decode("utf-8", errors="ignore")
                if fixed and fixed.count("Ã") < value.count("Ã"):
                    return fixed
            except Exception:
                pass
    return value


def absolute_url(base_url, link):
    return urllib.parse.urljoin(base_url, html.unescape(link or "").strip())


def fix_mojibake(value):
    if not value:
        return ""
    markers = ("\u00c3", "\u00c2", "\u00e2", "\ufffd")
    if not any(marker in value for marker in markers):
        return value

    def score(text):
        return sum(text.count(marker) for marker in markers)

    best = value
    best_score = score(value)
    for source_encoding in ("latin1", "cp1252"):
        try:
            fixed = value.encode(source_encoding, errors="ignore").decode("utf-8", errors="ignore")
            fixed_score = score(fixed)
            if fixed and fixed_score < best_score:
                best = fixed
                best_score = fixed_score
        except Exception:
            pass
    return best


def parse_date(value):
    if not value:
        return ""
    value = clean_text(value)
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        pass
    spanish_months = {
        "ene": 1, "enero": 1,
        "feb": 2, "febrero": 2,
        "mar": 3, "marzo": 3,
        "abr": 4, "abril": 4,
        "may": 5, "mayo": 5,
        "jun": 6, "junio": 6,
        "jul": 7, "julio": 7,
        "ago": 8, "agosto": 8,
        "sep": 9, "sept": 9, "septiembre": 9,
        "oct": 10, "octubre": 10,
        "nov": 11, "noviembre": 11,
        "dic": 12, "diciembre": 12
    }
    match = re.search(r"(\d{1,2})\s+([a-záéíóúñ\.]+)\s+(\d{4})", value.lower())
    if match:
        month_key = normalize_text(match.group(2).rstrip("."))
        month = spanish_months.get(month_key)
        if month:
            parsed = dt.datetime(int(match.group(3)), month, int(match.group(1)), tzinfo=dt.timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed = dt.datetime.strptime(value[:19], fmt)
            return parsed.replace(tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            continue
    return ""


def item_id(source_id, url, title):
    seed = (source_id + "|" + (url or "") + "|" + (title or "")).encode("utf-8", errors="ignore")
    return hashlib.sha256(seed).hexdigest()[:16]


def matches_keywords(title, summary, keywords):
    if not keywords:
        return True
    haystack = (title + " " + summary).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def matches_required_any(title, summary, required_any):
    if not required_any:
        return True
    haystack = normalize_text(title + " " + summary)
    return any(normalize_text(keyword) in haystack for keyword in required_any)


def is_relevant_item(item):
    haystack = normalize_text(" ".join([
        item.get("source", ""),
        item.get("title", ""),
        item.get("summary", ""),
        item.get("url", "")
    ]))
    if any(normalize_text(term) in haystack for term in COMPANY_TERMS):
        return True
    return any(normalize_text(term) in haystack for term in SECTOR_TERMS)


def normalize_text(value):
    value = clean_text(value).lower()
    value = value.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ü", "u").replace("ñ", "n")
    return value


def parse_rss(source, text):
    root = ET.fromstring(text)
    items = []
    candidates = root.findall(".//item")
    if not candidates:
        candidates = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for node in candidates:
        title = clean_text(child_text(node, "title"))
        summary = clean_text(child_text(node, "description") or child_text(node, "summary") or child_text(node, "content"))
        link = clean_text(child_text(node, "link"))
        if not link:
            link_node = node.find("{http://www.w3.org/2005/Atom}link")
            if link_node is not None:
                link = link_node.attrib.get("href", "")
        published = parse_date(child_text(node, "pubDate") or child_text(node, "published") or child_text(node, "updated"))
        if not title or not link:
            continue
        if not matches_keywords(title, summary, source.get("keywords", [])):
            continue
        if not matches_required_any(title, summary, source.get("requiredAny", [])):
            continue
        items.append({
            "id": item_id(source["id"], link, title),
            "source": source["name"],
            "title": title[:160],
            "summary": summary[:260] if summary else "",
            "url": link,
            "publishedAt": published,
            "kind": "fuente_publica"
        })
    return items


def parse_ugt_granada_html(source, text):
    items = []
    base_url = source["url"]
    seen = set()
    pattern = re.compile(
        r'<a\s+href="(?P<link>https://www\.ugt-andalucia\.com/web/granada/w/[^"]+)"[^>]*>\s*(?P<title>.*?)\s*</a>',
        re.I | re.S
    )
    for match in pattern.finditer(text):
        link = absolute_url(base_url, match.group("link"))
        title = clean_text(match.group("title"))
        if not title or link in seen:
            continue
        seen.add(link)
        window = text[match.end():match.end() + 2500]
        summary_match = re.search(r'views-field-entradilla[^>]*>.*?<p[^>]*>(.*?)</p>', window, re.I | re.S)
        date_match = re.search(r'<time[^>]*>(.*?)</time>', window, re.I | re.S)
        summary = clean_text(summary_match.group(1)) if summary_match else ""
        if not matches_keywords(title, summary, source.get("keywords", [])):
            continue
        if not matches_required_any(title, summary, source.get("requiredAny", [])):
            continue
        items.append({
            "id": item_id(source["id"], link, title),
            "source": source["name"],
            "title": title[:160],
            "summary": summary[:260],
            "url": link,
            "publishedAt": parse_date(clean_text(date_match.group(1)) if date_match else ""),
            "kind": "ugt_granada"
        })
    return items[:source.get("limit", 8)]


def parse_ccoo_andalucia_html(source, text):
    items = []
    base_url = source["url"]
    seen = set()
    pattern = re.compile(
        r'<div class="titular">.*?<a\s+href="(?P<link>[^"]*noticia:[^"]+)"[^>]*>.*?<span[^>]*>(?P<title>.*?)</span>.*?</a>.*?</div>(?P<tail>.{0,1800})',
        re.I | re.S
    )
    for match in pattern.finditer(text):
        link = absolute_url(base_url, match.group("link"))
        title = clean_text(match.group("title"))
        if not title or link in seen:
            continue
        seen.add(link)
        tail = match.group("tail")
        summary_match = re.search(r"(?:<div class=['\"]entradilla['\"][^>]*>|<p[^>]*>)(.*?)(?:</div>|</p>)", tail, re.I | re.S)
        summary = clean_text(summary_match.group(1)) if summary_match else ""
        if not matches_keywords(title, summary, source.get("keywords", [])):
            continue
        if not matches_required_any(title, summary, source.get("requiredAny", [])):
            continue
        items.append({
            "id": item_id(source["id"], link, title),
            "source": source["name"],
            "title": title[:160],
            "summary": summary[:260],
            "url": link,
            "publishedAt": parse_date(""),
            "kind": "ccoo_granada"
        })
    return items[:source.get("limit", 8)]


def child_text(node, name):
    found = node.find(name)
    if found is not None and found.text:
        return found.text
    for child in list(node):
        if child.tag.endswith("}" + name) and child.text:
            return child.text
    return ""


def inspect_html_watch(source, text):
    lowered = text.lower()
    blocked_terms = ["login", "iniciar sesion", "cookies", "checkpoint", "unsupported browser", "meta content="]
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    title = clean_text(title_match.group(1)) if title_match else ""
    blocked = len(text) < 500 or any(term in lowered for term in blocked_terms)
    return {
        "id": source["id"],
        "name": source["name"],
        "ok": not blocked,
        "mode": "watch",
        "message": "Fuente accesible" if not blocked else "Fuente limitada o bloqueada; no se publica nada automatico",
        "title": title[:120]
    }


def meta_config(source):
    token_env = source.get("accessTokenEnv", "META_ACCESS_TOKEN")
    token = os.environ.get(token_env, "").strip()
    version = os.environ.get("META_GRAPH_VERSION", "v24.0").strip() or "v24.0"
    return token, version


def missing_meta_status(source, missing):
    return {
        "id": source["id"],
        "name": source["name"],
        "ok": False,
        "mode": source.get("type", "meta"),
        "items": 0,
        "message": "Meta API preparada, pero falta configurar: " + ", ".join(missing)
    }


def parse_meta_page_posts(source):
    token, version = meta_config(source)
    page_id = os.environ.get(source.get("pageIdEnv", ""), "").strip()
    missing = []
    if not token:
        missing.append(source.get("accessTokenEnv", "META_ACCESS_TOKEN"))
    if not page_id:
        missing.append(source.get("pageIdEnv", "PAGE_ID"))
    if missing:
        return [], missing_meta_status(source, missing)

    root = fetch_json(
        f"https://graph.facebook.com/{version}/{page_id}/posts",
        {
            "fields": "id,message,created_time,permalink_url",
            "limit": str(source.get("limit", 10)),
            "access_token": token
        }
    )
    items = []
    for post in root.get("data", []):
        title = clean_text(post.get("message", "")).split(". ")[0][:120] or "Publicacion de " + source["name"]
        summary = clean_text(post.get("message", ""))
        if not matches_required_any(title, summary, source.get("requiredAny", [])):
            continue
        link = post.get("permalink_url", "")
        items.append({
            "id": item_id(source["id"], link or post.get("id", ""), title),
            "source": source["name"],
            "title": title[:160],
            "summary": summary[:260],
            "url": link,
            "publishedAt": parse_date(post.get("created_time", "")),
            "kind": "meta_facebook"
        })
    return items, {
        "id": source["id"],
        "name": source["name"],
        "ok": True,
        "mode": "meta_page_posts",
        "items": len(items),
        "message": "OK"
    }


def parse_meta_instagram_media(source):
    token, version = meta_config(source)
    ig_user_id = os.environ.get(source.get("instagramUserIdEnv", ""), "").strip()
    missing = []
    if not token:
        missing.append(source.get("accessTokenEnv", "META_ACCESS_TOKEN"))
    if not ig_user_id:
        missing.append(source.get("instagramUserIdEnv", "INSTAGRAM_USER_ID"))
    if missing:
        return [], missing_meta_status(source, missing)

    root = fetch_json(
        f"https://graph.facebook.com/{version}/{ig_user_id}/media",
        {
            "fields": "id,caption,permalink,timestamp,media_type",
            "limit": str(source.get("limit", 10)),
            "access_token": token
        }
    )
    items = []
    for media in root.get("data", []):
        caption = clean_text(media.get("caption", ""))
        title = caption.split(". ")[0][:120] or "Publicacion de " + source["name"]
        if not matches_required_any(title, caption, source.get("requiredAny", [])):
            continue
        link = media.get("permalink", "")
        items.append({
            "id": item_id(source["id"], link or media.get("id", ""), title),
            "source": source["name"],
            "title": title[:160],
            "summary": caption[:260],
            "url": link,
            "publishedAt": parse_date(media.get("timestamp", "")),
            "kind": "meta_instagram"
        })
    return items, {
        "id": source["id"],
        "name": source["name"],
        "ok": True,
        "mode": "meta_instagram_media",
        "items": len(items),
        "message": "OK"
    }


def merge_items(old_items, new_items):
    merged = {}
    for item in old_items + new_items:
        if not item.get("id"):
            continue
        if not is_relevant_item(item):
            continue
        merged[item["id"]] = item
    return sorted(merged.values(), key=lambda item: item.get("publishedAt", ""), reverse=True)[:MAX_ITEMS]


def main():
    sources_config = load_json(SOURCES_FILE, {"sources": []})
    old_news = load_json(NEWS_FILE, {"items": []})
    now = utc_now()
    collected = []
    source_status = []

    for source in sources_config.get("sources", []):
        try:
            source_type = source.get("type")
            if source_type == "meta_page_posts":
                items, status = parse_meta_page_posts(source)
                collected.extend(items)
                source_status.append(status)
            elif source_type == "meta_instagram_media":
                items, status = parse_meta_instagram_media(source)
                collected.extend(items)
                source_status.append(status)
            else:
                text, content_type = fetch(source["url"])
                if source_type == "ugt_granada_html":
                    items = parse_ugt_granada_html(source, text)
                    collected.extend(items)
                    source_status.append({
                        "id": source["id"],
                        "name": source["name"],
                        "ok": True,
                        "mode": "ugt_granada_html",
                        "items": len(items),
                        "message": "OK"
                    })
                elif source_type == "ccoo_andalucia_html":
                    items = parse_ccoo_andalucia_html(source, text)
                    collected.extend(items)
                    source_status.append({
                        "id": source["id"],
                        "name": source["name"],
                        "ok": True,
                        "mode": "ccoo_andalucia_html",
                        "items": len(items),
                        "message": "OK"
                    })
                elif source_type == "rss":
                    items = parse_rss(source, text)
                    collected.extend(items)
                    source_status.append({
                        "id": source["id"],
                        "name": source["name"],
                        "ok": True,
                        "mode": "rss",
                        "items": len(items),
                        "message": "OK"
                    })
                elif source_type == "html_watch":
                    source_status.append(inspect_html_watch(source, text))
                else:
                    source_status.append({
                        "id": source.get("id", "unknown"),
                        "name": source.get("name", "Fuente"),
                        "ok": False,
                        "message": "Tipo de fuente no soportado"
                    })
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as error:
            source_status.append({
                "id": source.get("id", "unknown"),
                "name": source.get("name", "Fuente"),
                "ok": False,
                "message": f"No se pudo leer la fuente: {type(error).__name__}"
            })
        except Exception as error:
            source_status.append({
                "id": source.get("id", "unknown"),
                "name": source.get("name", "Fuente"),
                "ok": False,
                "message": f"Error controlado: {type(error).__name__}"
            })

    items = merge_items(old_news.get("items", []), collected)
    any_ok = any(status.get("ok") for status in source_status)
    news = {
        "version": 1,
        "generatedAt": now,
        "status": "ok" if any_ok else "degraded",
        "message": "Noticias actualizadas desde fuentes publicas." if any_ok else "No se pudo actualizar ninguna fuente. Se mantienen las noticias anteriores.",
        "items": items
    }
    status = {
        "lastRun": now,
        "ok": any_ok,
        "sources": source_status,
        "notes": [
            "El robot ignora fuentes bloqueadas o raras.",
            "Las fuentes html_watch solo se vigilan; no publican contenido automaticamente."
        ]
    }
    write_json_if_changed(NEWS_FILE, news)
    write_json_if_changed(STATUS_FILE, status)
    print(f"Robot terminado: {len(collected)} noticias nuevas candidatas, {len(items)} guardadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
