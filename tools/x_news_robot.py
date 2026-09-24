#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NEWS_FILE = ROOT / "data" / "noticias.json"
USERNAME = "UGT_LAPALMA"
SOURCE_ID = "ugt-granada-la-palma-x"
SOURCE_NAME = "UGT Granada La Palma · @UGT_LAPALMA"
PROFILE_URL = f"https://x.com/{USERNAME}"
MAX_POSTS = 4
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_bytes(url, accept="*/*"):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content_type = response.headers.get("content-type", "").lower()
        payload = response.read(12_000_000)
    return payload, content_type


def fetch_profile_html():
    payload, content_type = fetch_bytes(PROFILE_URL, "text/html,*/*;q=0.8")
    if "html" not in content_type or len(payload) < 20_000:
        raise RuntimeError("X no devolvio el perfil publico completo")
    return payload.decode("utf-8", errors="replace")


def decode_js_string(raw):
    try:
        return json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return raw.replace("\\n", "\n").replace("\\\"", '"').replace("\\/", "/")


def visible_post_text(value):
    value = (value or "").strip()
    value = re.sub(r"(?:^|\s+)(?:https://t\.co/[A-Za-z0-9]+)(?:\s+https://t\.co/[A-Za-z0-9]+)*\s*$", "", value).strip()
    return value


def medium_image_url(url):
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    extension = Path(parsed.path).suffix.lstrip(".") or "jpg"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, f"format={extension}&name=medium", ""))


def extract_original_posts(page_html):
    timeline_start = page_html.find("profile_user_originals_timeline")
    if timeline_start < 0:
        raise RuntimeError("X no devolvio la cronologia de publicaciones originales")
    page_html = page_html[timeline_start:]
    posts = {}
    for entry in re.finditer(r'entry_id:"tweet-(\d+)"', page_html):
        post_id = entry.group(1)
        marker = f'rest_id:"{post_id}",result:'
        start = page_html.rfind(marker, 0, entry.start())
        if start < 0:
            continue
        chunk = page_html[start:entry.start()]
        if f'screen_name:"{USERNAME}"' not in chunk:
            continue
        created_match = re.search(r"created_at_ms:(\d+)", chunk)
        text_match = re.search(r'full_text:"((?:\\.|[^"\\])*)"', chunk)
        image_match = re.search(r'media_url_https:"((?:\\.|[^"\\])*)"', chunk)
        if not created_match or not text_match:
            continue
        created_at = dt.datetime.fromtimestamp(
            int(created_match.group(1)) / 1000,
            tz=dt.timezone.utc,
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        posts[post_id] = {
            "postId": post_id,
            "text": visible_post_text(decode_js_string(text_match.group(1))),
            "publishedAt": created_at,
            "imageUrl": medium_image_url(decode_js_string(image_match.group(1))) if image_match else "",
        }
    result = sorted(posts.values(), key=lambda post: int(post["postId"]), reverse=True)
    if len(result) < MAX_POSTS:
        raise RuntimeError(f"X solo devolvio {len(result)} publicaciones originales; se conserva el feed anterior")
    return result[:MAX_POSTS]


def fetch_original_posts_with_browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("X bloqueo la descarga y Playwright no esta instalado") from error

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        try:
            page = browser.new_page(user_agent=USER_AGENT, locale="es-ES")
            page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_selector("article", timeout=40_000)
            collected = {}
            for _ in range(5):
                raw_posts = page.locator("article").evaluate_all(
                    r"""articles => articles.map(article => {
                      const links = Array.from(article.querySelectorAll('a[href]')).map(a => a.href);
                      const status = links.find(href => /\/UGT_LAPALMA\/status\/\d+(?:$|[/?])/.test(href));
                      const id = status ? (status.match(/\/status\/(\d+)/) || [])[1] : '';
                      const textNode = article.querySelector('[data-testid="tweetText"]');
                      const timeNode = article.querySelector('time[datetime]');
                      const imageNode = article.querySelector('img[src*="pbs.twimg.com/media"]');
                      const socialNode = article.querySelector('[data-testid="socialContext"]');
                      return {
                        postId: id || '',
                        text: textNode ? textNode.innerText : '',
                        publishedAt: timeNode ? timeNode.getAttribute('datetime') : '',
                        imageUrl: imageNode ? imageNode.src : '',
                        socialContext: socialNode ? socialNode.innerText : ''
                      };
                    })"""
                )
                for post in raw_posts:
                    context = (post.get("socialContext") or "").casefold()
                    if not post.get("postId") or "repost" in context or "republic" in context:
                        continue
                    post["text"] = visible_post_text(post.get("text", ""))
                    post["publishedAt"] = parse_iso_date(post.get("publishedAt", ""))
                    post["imageUrl"] = medium_image_url(post.get("imageUrl", ""))
                    if post["publishedAt"]:
                        collected[post["postId"]] = post
                if len(collected) >= MAX_POSTS:
                    break
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(1500)
        finally:
            browser.close()
    posts = sorted(collected.values(), key=lambda post: int(post["postId"]), reverse=True)
    if len(posts) < MAX_POSTS:
        raise RuntimeError(f"El navegador solo encontro {len(posts)} publicaciones originales")
    return posts[:MAX_POSTS]


def parse_iso_date(value):
    if not value:
        return ""
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_original_posts():
    try:
        return extract_original_posts(fetch_profile_html())
    except (urllib.error.URLError, RuntimeError) as direct_error:
        print(f"Lectura directa no disponible ({type(direct_error).__name__}); se usa Chromium.")
        return fetch_original_posts_with_browser()


def clean_ocr_text(value):
    lines = []
    seen = set()
    for raw_line in (value or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" -_|[]")
        if len(line) < 2:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines)


def read_image_text(image_bytes, suffix=".jpg"):
    executable = shutil.which("tesseract")
    if not executable:
        return ""
    with tempfile.NamedTemporaryFile(suffix=suffix) as image_file:
        image_file.write(image_bytes)
        image_file.flush()
        for language in ("spa+eng", "eng"):
            completed = subprocess.run(
                [executable, image_file.name, "stdout", "-l", language, "--psm", "6"],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            text = clean_ocr_text(completed.stdout)
            if completed.returncode == 0 and text:
                return text
    return ""


def inspect_image(url):
    if not url:
        return ""
    payload, content_type = fetch_bytes(url, "image/*")
    if not content_type.startswith("image/") or len(payload) < 500:
        raise RuntimeError(f"La imagen publica no es valida: {url}")
    suffix = "." + (content_type.split("/", 1)[1].split(";", 1)[0] or "jpg")
    return read_image_text(payload, suffix)


def first_useful_line(post_text, image_text, published_at):
    candidates = []
    for value in (post_text, image_text):
        candidates.extend(line.strip() for line in value.splitlines() if line.strip())
    ignored = {"ugt", "ugt granada la palma", "comunicado", "aviso"}
    for line in candidates:
        normalized = re.sub(r"[^a-z0-9]+", " ", line.casefold()).strip()
        if normalized in ignored or len(line) < 6:
            continue
        return line[:105].rstrip(" .")
    if candidates:
        return candidates[0][:105].rstrip(" .")
    date_text = dt.datetime.fromisoformat(published_at.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    return "Publicacion sindical del " + date_text


def combined_summary(post_text, image_text):
    values = []
    for value in (post_text, image_text):
        compact = re.sub(r"\s+", " ", value or "").strip()
        if compact and compact.casefold() not in {item.casefold() for item in values}:
            values.append(compact)
    return " ".join(values)[:900]


def build_items(posts, old_items):
    old_by_post_id = {str(item.get("postId", "")): item for item in old_items}
    items = []
    for post in posts:
        image_text = inspect_image(post["imageUrl"]) if post["imageUrl"] else ""
        old = old_by_post_id.get(post["postId"], {})
        title = first_useful_line(post["text"], image_text, post["publishedAt"])
        summary = combined_summary(post["text"], image_text)
        if not image_text and old:
            title = old.get("title") or title
            summary = old.get("summary") or summary
        items.append({
            "id": "x-" + post["postId"],
            "sourceId": SOURCE_ID,
            "postId": post["postId"],
            "source": SOURCE_NAME,
            "title": title,
            "summary": summary,
            "url": f"https://x.com/{USERNAME}/status/{post['postId']}",
            "publishedAt": post["publishedAt"],
            "kind": "x_post",
            "imageUrl": post["imageUrl"],
            "imageAlt": "Cartel publicado por UGT Granada La Palma: " + title,
        })
    return items


def load_news():
    return json.loads(NEWS_FILE.read_text(encoding="utf-8"))


def update_news(check_only=False):
    previous = load_news()
    posts = fetch_original_posts()
    items = build_items(posts, previous.get("items", []))
    if previous.get("items", []) == items:
        print("Sin cambios: las cuatro publicaciones de X ya estan actualizadas.")
        return False
    news = {
        "version": 1,
        "generatedAt": utc_now(),
        "status": "ok",
        "message": "Noticias actualizadas desde fuentes publicas.",
        "items": items,
    }
    json.dumps(news, ensure_ascii=False)
    if check_only:
        print("Comprobacion correcta: hay cambios, pero no se ha escrito el JSON.")
        return True
    NEWS_FILE.write_text(json.dumps(news, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Noticias actualizadas: " + ", ".join(item["title"] for item in items))
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Comprueba X sin modificar noticias.json")
    args = parser.parse_args()
    update_news(check_only=args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
