#!/usr/bin/env python3
"""Robot laboral de Control de Horas LAP.

Cada dia revisa:
- el BOE, por si sale un Real Decreto nuevo del SMI;
- todos los boletines del BOP de Granada desde la ultima revision, buscando anuncios
  del convenio de manipulado y envasado de frutas, hortalizas y patata.

Si un anuncio trae la tabla salarial, la publica en data/laboral.json.
Si trae el texto completo del convenio, regenera data/convenio_frutas_granada.json.
Si trae otra cosa del convenio (revision, calendario, acuerdo...), lo publica como aviso.
Todo lo que hace, y cualquier anuncio que no sepa leer, lo deja en robot_report.md para
que el workflow abra un aviso (issue) en GitHub y llegue por correo.
"""
import datetime as dt
import hashlib
import html
import io
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
LABOR_FILE = ROOT / "data" / "laboral.json"
CONVENTION_FILE = ROOT / "data" / "convenio_frutas_granada.json"
STATUS_FILE = ROOT / "data" / "labor_robot_status.json"
REPORT_FILE = ROOT / "robot_report.md"
USER_AGENT = "ControlHorasLAP-LaborRobot/2.0 (+https://github.com/Jose-Controldehoras/control-de-horas-lap)"
BOE_SUMMARY_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{date}"
BOP_BASE = "https://bop-admin.dipgra.es"
BOP_DAY_URL = BOP_BASE + "/publica/consulta-de-bops/buscador/BOP-{date}/"
MAX_DAYS_BACK = 60
FAILURES_BEFORE_ALERT = 4

report = []


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
    if old == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def fetch_bytes(url, accept="*/*"):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(request, timeout=40) as response:
        return response.read()


def fetch_text(url, accept="text/html,application/xml;q=0.9,*/*;q=0.8"):
    return fetch_bytes(url, accept).decode("utf-8", errors="replace")


def normalized(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).strip().lower()


def decimal(value):
    return float(value.replace(".", "").replace(",", "."))


def find_amount(text, pattern):
    match = re.search(pattern, text, re.I | re.S)
    if not match:
        raise ValueError("No se encontro un importe obligatorio")
    return decimal(match.group(1))


def upsert_news(labor, item):
    news = labor.setdefault("news", [])
    news[:] = [old for old in news if old.get("id") != item.get("id")]
    news.append(item)
    news.sort(key=lambda old: old.get("publishedAt", ""), reverse=True)


# ---------------------------------------------------------------- BOE (SMI)

def parse_boe_smi_document(item, publication_date):
    document_id = item.findtext("identificador", "").strip()
    title = item.findtext("titulo", "").strip()
    url = item.findtext("url_html", "").strip()
    if not url:
        url = f"https://www.boe.es/buscar/act.php?id={urllib.parse.quote(document_id)}"
    body = normalized(fetch_text(url))
    daily = find_amount(body, r"(\d{1,3},\d{1,2})\s*euros?\s*/?\s*dia")
    monthly = find_amount(body, r"(\d[\d\.,]*)\s*euros?\s*/?\s*mes")
    year_match = re.search(r"para\s+(\d{4})", normalized(title))
    year = int(year_match.group(1)) if year_match else publication_date.year
    annual_match = re.search(r"computo anual[^0-9]{0,120}(\d[\d\.,]*)\s*euros", body, re.I | re.S)
    annual = decimal(annual_match.group(1)) if annual_match else round(monthly * 14, 2)
    effective_match = re.search(r"efectos?[^.]{0,100}?(\d{1,2})\s+de\s+enero\s+de\s+(\d{4})", body)
    effective_year = int(effective_match.group(2)) if effective_match else year
    effective_day = int(effective_match.group(1)) if effective_match else 1
    effective = f"{effective_year:04d}-01-{effective_day:02d}"
    if not (20 <= daily <= 100 and 600 <= monthly <= 3000 and 8000 <= annual <= 50000):
        raise ValueError("Importes SMI fuera de rango")
    return {
        "revision": f"smi-{year}-{document_id.lower()}",
        "year": year,
        "daily": round(daily, 2),
        "monthly": round(monthly, 2),
        "annual": round(annual, 2),
        "effectiveFrom": effective,
        "publishedAt": publication_date.isoformat(),
        "reference": document_id,
        "officialUrl": f"https://www.boe.es/buscar/act.php?id={document_id}",
    }


def scan_boe(labor, status, today):
    try:
        last = dt.datetime.strptime(status.get("lastBoeScan", ""), "%Y%m%d").date()
    except Exception:
        last = today - dt.timedelta(days=14)
    start = max(last + dt.timedelta(days=1), today - dt.timedelta(days=MAX_DAYS_BACK))
    found = []
    day = start
    while day <= today:
        try:
            xml = fetch_bytes(BOE_SUMMARY_URL.format(date=day.strftime("%Y%m%d")), "application/xml")
            root = ET.fromstring(xml)
            for item in root.findall(".//item"):
                if "salario minimo interprofesional para" in normalized(item.findtext("titulo", "")):
                    found.append(parse_boe_smi_document(item, day))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        status["lastBoeScan"] = day.strftime("%Y%m%d")
        day += dt.timedelta(days=1)

    changed = False
    for record in found:
        values = labor.setdefault("smi", [])
        if any(old.get("revision") == record["revision"] for old in values):
            continue
        values[:] = [old for old in values if old.get("year") != record["year"]]
        values.append(record)
        values.sort(key=lambda old: old.get("year", 0))
        upsert_news(labor, {
            "id": f"labor-smi-{record['year']}",
            "kind": "official_update",
            "source": "Actualizacion laboral oficial",
            "title": f"SMI {record['year']} actualizado en la app",
            "summary": (
                f"El SMI oficial es de {record['daily']:.2f} EUR al dia y "
                f"{record['monthly']:.2f} EUR al mes, con efectos desde {record['effectiveFrom']}."
            ),
            "publishedAt": record["publishedAt"] + "T00:00:00Z",
            "effectiveFrom": record["effectiveFrom"],
            "url": record["officialUrl"],
        })
        report.append(
            f"- **SMI {record['year']} publicado:** {record['daily']:.2f} EUR/dia, "
            f"{record['monthly']:.2f} EUR/mes, desde {record['effectiveFrom']} ({record['officialUrl']})."
        )
        changed = True
    status["boe"] = {"ok": True, "message": f"BOE revisado desde {start.isoformat()}. {len(found)} SMI encontrados."}
    return changed


# ------------------------------------------------------------- tabla salarial

def pdf_reader(pdf_bytes):
    return PdfReader(io.BytesIO(pdf_bytes))


def pdf_text(pdf_bytes):
    return "\n".join(page.extract_text() or "" for page in pdf_reader(pdf_bytes).pages)


def parse_rate_section(section):
    return {
        "extra": find_amount(section, r"VALOR HORA EXTRA\s+(\d+,\d+)"),
        "night22": find_amount(section, r"VALOR HORA NOCTURNA DE 22\.00 A 23\.00 H\s+(\d+,\d+)"),
        "nightRest": find_amount(section, r"VALOR HORA NOCTURNA RESTO DE CONCEPTOS\s+(\d+,\d+)"),
        "extraNight22": find_amount(section, r"VALOR HORA EXTRA \+ NOCTURNA DE 22\.00 A 23\.00 H\s+(\d+,\d+)"),
        "extraNightRest": find_amount(section, r"VALOR HORA EXTRA \+ NOCTURNA RESTO DE\s+CONCEPTOS\s+(\d+,\d+)"),
    }


def has_salary_table(text):
    upper = re.sub(r"\s+", " ", text).upper()
    return "VALOR HORA ORDINARIA" in upper or "TABLAS SALARIALES" in upper


def parse_bop_salary_table(text, publication_date, official_url, reference):
    clean = re.sub(r"\s+", " ", text)
    upper = clean.upper()
    ordinary = upper.find("VALOR HORA ORDINARIA")
    mozo_section_start = upper.find("VALOR HORA EXTRA, NOCTURNA Y FESTIVAS", ordinary)
    jefe_section_start = upper.find("VALOR HORA EXTRA, NOCTURNA Y FESTIVAS", mozo_section_start + 20)
    carencia_start = upper.find("PLUS CARENCIA", jefe_section_start)
    if min(ordinary, mozo_section_start, jefe_section_start, carencia_start) < 0:
        raise ValueError("Hay tabla salarial, pero no con el formato de siempre")

    ordinary_text = clean[ordinary:mozo_section_start]
    mozo = parse_rate_section(clean[mozo_section_start:jefe_section_start])
    jefe = parse_rate_section(clean[jefe_section_start:carencia_start])
    carencia = clean[carencia_start:]
    mozo["normal"] = find_amount(ordinary_text, r"MANIPULADORA Y MOZO DE ALMAC[ÉE]N\s+(\d+,\d+)")
    jefe["normal"] = find_amount(ordinary_text, r"JEFA DE L[ÍI]NEA Y JEFE DE EQUIPO\s+(\d+,\d+)")
    mozo["carencia"] = find_amount(carencia, r"MANIPULADORA Y MOZO DE ALMAC[ÉE]N\s+(\d+,\d+)")
    jefe["carencia"] = find_amount(carencia, r"JEFA DE L[ÍI]NEA Y JEFE DE EQUIPO\s+(\d+,\d+)")

    effective = None
    match = re.search(r"aplicaci[óo]n a partir de[l]?\s+(\d{1,2})/(\d{1,2})/(\d{4})", clean, re.I)
    if match:
        effective = dt.date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    else:
        match = re.search(r"a partir del?\s+(\d{1,2}) de enero de (\d{4})", clean, re.I)
        if match:
            effective = dt.date(int(match.group(2)), 1, int(match.group(1)))
    if effective is None:
        effective = dt.date(publication_date.year, 1, 1)

    digest = hashlib.sha256(json.dumps({"mozo": mozo, "jefe": jefe}, sort_keys=True).encode()).hexdigest()[:10]
    return {
        "revision": f"frutas-granada-{effective.year}-{digest}",
        "effectiveFrom": effective.isoformat(),
        "publishedAt": publication_date.isoformat(),
        "reference": reference,
        "officialUrl": official_url,
        "summary": "Tabla salarial oficial publicada en el BOP de Granada, leida y comprobada por el robot.",
        "rates": {"mozo": mozo, "jefe": jefe},
    }


def salary_table_problems(table, current):
    problems = []
    for name, rates in table["rates"].items():
        if not all(0 < value < 100 for value in rates.values()):
            problems.append(f"{name}: hay importes fuera de 0-100 EUR/h")
        if not rates["normal"] < rates["extra"] < rates["extraNightRest"]:
            problems.append(f"{name}: la hora extra no queda entre la normal y la extra nocturna")
        if not rates["normal"] <= rates["night22"] <= rates["nightRest"]:
            problems.append(f"{name}: las nocturnas no cuadran con la normal")
    if table["rates"]["jefe"]["normal"] <= table["rates"]["mozo"]["normal"]:
        problems.append("la hora de jefe/a no es mayor que la de mozo/a")
    old = current.get("rates", {}) if current else {}
    for name in ("mozo", "jefe"):
        before = old.get(name, {}).get("normal")
        after = table["rates"][name]["normal"]
        if before and not (before * 0.97 <= after <= before * 1.30):
            problems.append(f"{name}: la hora normal pasa de {before:.2f} a {after:.2f} (cambio raro)")
    if current and table["effectiveFrom"] < current.get("effectiveFrom", ""):
        problems.append(f"la fecha de efectos {table['effectiveFrom']} es anterior a la vigente")
    return problems


def describe_table(table, current):
    old = current.get("rates", {}) if current else {}
    lines = ["| Concepto | Mozo/a | Jefe/a |", "| --- | --- | --- |"]
    labels = [
        ("normal", "Hora normal"), ("carencia", "Plus carencia"), ("night22", "Nocturna 22-23 h"),
        ("nightRest", "Nocturna resto"), ("extra", "Extra"), ("extraNight22", "Extra nocturna 22-23 h"),
        ("extraNightRest", "Extra nocturna resto"),
    ]
    for key, label in labels:
        cells = []
        for name in ("mozo", "jefe"):
            value = table["rates"][name][key]
            before = old.get(name, {}).get(key)
            cells.append(f"{value:.2f}" + (f" (antes {before:.2f})" if before is not None and before != value else ""))
        lines.append(f"| {label} | {cells[0]} | {cells[1]} |")
    return "\n".join(lines)


# ------------------------------------------------------------ texto convenio

NOISE = [
    re.compile(r"^P[áa]gina \d+ de( un total de)? \d+\s*$", re.I),
    re.compile(r"^CVE:", re.I),
    re.compile(r"^N[ºo°]\s*\d+\s*[-/]", re.I),
    re.compile(r"^BOLET[ÍI]N OFICIAL DE LA PROVINCIA", re.I),
    re.compile(r"^Bolet[íi]n Oficial de la Provincia", re.I),
    re.compile(r"^\s*$"),
]
ARTICLE = re.compile(r"^ART[ÍI]CULO\s+(\d+)\.?\s*[.:\-–]?\s*(.*)$")
SPECIAL = re.compile(r"^((?:DISPOSICI[ÓO]N(?:ES)?\s+(?:ADICIONAL|TRANSITORIA|FINAL|DEROGATORIA)\w*|ANEXO\s+[IVXL]+)\b.*)$")


def clean_spaces(text):
    return re.sub(r"\s+", " ", text).strip()


def build_convention_records(reader):
    """Separa el convenio en articulos, disposiciones y anexos, igual que el archivo publicado."""
    records = []
    current = None
    special_count = 0
    for page_number, page in enumerate(reader.pages, start=1):
        for raw in (page.extract_text() or "").splitlines():
            line = raw.strip()
            if any(pattern.search(line) for pattern in NOISE):
                continue
            article = ARTICLE.match(line)
            special = SPECIAL.match(line) if not article else None
            if article:
                current = {
                    "id": f"article-{int(article.group(1))}", "kind": "article",
                    "number": int(article.group(1)), "title": clean_spaces(article.group(2)),
                    "pageStart": page_number, "pageEnd": page_number, "lines": [],
                }
                records.append(current)
                continue
            if special and records:
                special_count += 1
                title = clean_spaces(special.group(1))
                current = {
                    "id": f"special-{special_count}-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
                    "kind": "annex" if title.upper().startswith("ANEXO") else "provision",
                    "number": 0, "title": title,
                    "pageStart": page_number, "pageEnd": page_number, "lines": [],
                }
                records.append(current)
                continue
            if current is not None:
                current["lines"].append(line)
                current["pageEnd"] = page_number
    for record in records:
        record["text"] = clean_spaces(" ".join(record.pop("lines")))
    return records


def convention_problems(records):
    numbers = [r["number"] for r in records if r["kind"] == "article"]
    problems = []
    if len(numbers) < 30:
        problems.append(f"solo se encontraron {len(numbers)} articulos")
    elif numbers != list(range(1, len(numbers) + 1)):
        problems.append("los articulos no van seguidos del 1 al ultimo")
    empty = [r["id"] for r in records if len(r["text"]) < 20]
    if empty:
        problems.append("apartados vacios: " + ", ".join(empty[:5]))
    return problems


def convention_validity(records):
    article4 = next((r["text"] for r in records if r["id"] == "article-4"), "")
    dates = re.findall(r"(\d{1,2}) de (enero|diciembre) de (\d{4})", article4, re.I)
    start = next((f"{y}-01-{int(d):02d}" for d, m, y in dates if m.lower() == "enero"), "")
    end = next((f"{y}-12-{int(d):02d}" for d, m, y in dates if m.lower() == "diciembre"), "")
    return start, end


# ---------------------------------------------------------------- BOP Granada

def relevant_title(title):
    text = normalized(title)
    return "frutas" in text and ("hortaliza" in text or "manipulado" in text or "envasado" in text)


def bop_announcements(day):
    """Anuncios del boletin de ese dia. None si ese dia no hubo boletin."""
    try:
        page = fetch_text(BOP_DAY_URL.format(date=day.strftime("%d-%m-%Y")))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    items = []
    for block in re.findall(r'<li class="elementoListado.*?</li>', page, re.S):
        link = re.search(r'<a href="([^"]+)" title="([^"]*)"', block)
        title = re.search(r'<h3 class="titulo_elemento">(.*?)</h3>', block, re.S)
        cve = re.search(r"(BOP-GRA-\d{4}-\d+)", block)
        if not link:
            continue
        items.append({
            "url": urllib.parse.urljoin(BOP_BASE, html.unescape(link.group(1))),
            "title": clean_spaces(html.unescape(re.sub(r"<[^>]+>", " ", title.group(1) if title else link.group(2)))),
            "cve": cve.group(1) if cve else link.group(1),
        })
    return items if items else None


def announcement_pdf(url):
    page = fetch_text(url)
    match = re.search(r'href="([^"]*/Documentos-Anuncios-en-PDF/[^"]+\.pdf)"', page, re.I)
    if not match:
        raise ValueError("el anuncio no tiene PDF")
    return urllib.parse.urljoin(BOP_BASE, html.unescape(match.group(1)))


def process_announcement(labor, convention, item, day):
    """Devuelve (cambia_laboral, cambia_convenio)."""
    pdf_url = announcement_pdf(item["url"])
    pdf_bytes = fetch_bytes(pdf_url, "application/pdf")
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    reader = pdf_reader(pdf_bytes)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    header = f"**{item['title']}** ({item['cve']}, BOP del {day.strftime('%d/%m/%Y')})\n{pdf_url}"
    labor_changed = convention_changed = False
    handled = False

    if has_salary_table(text):
        current = labor.setdefault("salaryTables", {}).get("frutas_granada", {})
        try:
            table = parse_bop_salary_table(text, day, pdf_url, item["cve"])
            same = current.get("rates") == table["rates"] and current.get("effectiveFrom") == table["effectiveFrom"]
            if same:
                report.append(f"- {header}\n  Trae la misma tabla salarial que ya tiene la app; no cambia nada.")
            else:
                problems = salary_table_problems(table, current)
                if problems:
                    report.append(
                        f"- {header}\n  **Tabla salarial NO publicada**, porque algo no cuadra: "
                        + "; ".join(problems) + ".\n\n" + describe_table(table, current)
                        + "\n\n  Revisala a mano en el PDF."
                    )
                else:
                    labor["salaryTables"]["frutas_granada"] = table
                    mozo = table["rates"]["mozo"]
                    jefe = table["rates"]["jefe"]
                    upsert_news(labor, {
                        "id": f"labor-convenio-frutas-granada-{table['effectiveFrom'][:4]}",
                        "kind": "official_update",
                        "source": "Convenio oficial de Granada",
                        "title": "Nueva tabla salarial oficial",
                        "summary": (
                            f"La hora normal pasa a {mozo['normal']:.2f} EUR para manipulador/a y mozo/a y a "
                            f"{jefe['normal']:.2f} EUR para jefe/a de linea y de equipo, desde "
                            f"{table['effectiveFrom']}. La app te pedira confirmacion y calculara los atrasos."
                        ),
                        "publishedAt": day.isoformat() + "T00:00:00Z",
                        "effectiveFrom": table["effectiveFrom"],
                        "url": pdf_url,
                    })
                    labor_changed = True
                    report.append(
                        f"- {header}\n  **Tabla salarial nueva publicada en la app**, desde {table['effectiveFrom']}.\n\n"
                        + describe_table(table, current)
                    )
            handled = True
        except ValueError as error:
            report.append(f"- {header}\n  Parece traer tabla salarial, pero no la he podido leer ({error}). Revisala a mano.")
            handled = True

    records = build_convention_records(reader)
    if len([r for r in records if r["kind"] == "article"]) >= 30:
        if sha == convention.get("sourceSha256"):
            report.append(f"- {header}\n  Es el mismo texto del convenio que ya tiene la app.")
        else:
            problems = convention_problems(records)
            if problems:
                report.append(
                    f"- {header}\n  **Texto del convenio NO publicado**, porque algo no cuadra: "
                    + "; ".join(problems) + ". Revisalo a mano."
                )
            else:
                start, end = convention_validity(records)
                convention.clear()
                convention.update({
                    "version": 1,
                    "conventionId": f"manipulado-frutas-granada-{start[:4] or day.year}-{end[:4] or day.year}",
                    "title": "Convenio colectivo del manipulado y envasado de frutas, hortalizas y patata de Granada",
                    "reference": item["cve"],
                    "publishedAt": day.isoformat(),
                    "effectiveFrom": start,
                    "effectiveTo": end,
                    "remainsInForceUntilReplaced": True,
                    "officialUrl": pdf_url,
                    "sourceSha256": sha,
                    "pageCount": len(reader.pages),
                    "records": records,
                })
                upsert_news(labor, {
                    "id": f"labor-convenio-texto-{item['cve'].lower()}",
                    "kind": "official_update",
                    "source": "Convenio oficial de Granada",
                    "title": "Nuevo texto del convenio",
                    "summary": "Se ha publicado un convenio nuevo. Abre Convenio para ver que articulos cambian.",
                    "publishedAt": day.isoformat() + "T00:00:00Z",
                    "url": pdf_url,
                })
                labor_changed = convention_changed = True
                report.append(
                    f"- {header}\n  **Texto nuevo del convenio publicado en la app** "
                    f"({len(records)} apartados, vigencia {start or '?'} a {end or '?'})."
                )
        handled = True

    if not handled:
        upsert_news(labor, {
            "id": f"labor-bop-{item['cve'].lower()}",
            "kind": "official_update",
            "source": "BOP de Granada",
            "title": "Nuevo anuncio sobre el convenio",
            "summary": f"{item['title'][:220]}. Toca para leerlo en el BOP.",
            "publishedAt": day.isoformat() + "T00:00:00Z",
            "url": pdf_url,
        })
        labor_changed = True
        report.append(
            f"- {header}\n  Anuncio del convenio sin tabla ni texto completo. "
            "Lo he puesto como aviso en la app; revisa si cambia algo que la app calcule."
        )
    return labor_changed, convention_changed


def scan_bop(labor, convention, status, today):
    try:
        last = dt.date.fromisoformat(status.get("lastBopScan", ""))
    except Exception:
        last = today - dt.timedelta(days=14)
    day = max(last + dt.timedelta(days=1), today - dt.timedelta(days=MAX_DAYS_BACK))
    seen = status.setdefault("seenAnnouncements", [])
    labor_changed = convention_changed = False
    bulletins = 0
    relevant = 0
    while day <= today:
        items = bop_announcements(day)
        if items is None:
            if day < today:
                status["lastBopScan"] = day.isoformat()
            day += dt.timedelta(days=1)
            continue
        bulletins += 1
        for item in items:
            if not relevant_title(item["title"] + " " + item["url"]) or item["cve"] in seen:
                continue
            relevant += 1
            a, b = process_announcement(labor, convention, item, day)
            labor_changed |= a
            convention_changed |= b
            seen.append(item["cve"])
        status["lastBopScan"] = day.isoformat()
        day += dt.timedelta(days=1)
    del seen[:-200]
    status["bopGranada"] = {
        "ok": True,
        "message": f"Revisado hasta el {status.get('lastBopScan')}: {bulletins} boletines, {relevant} anuncios del convenio.",
    }
    return labor_changed, convention_changed


# ----------------------------------------------------------------------- main

def main():
    today = dt.date.today()
    labor = load_json(LABOR_FILE, {"version": 1, "smi": [], "salaryTables": {}, "news": []})
    convention = load_json(CONVENTION_FILE, {})
    status = load_json(STATUS_FILE, {})
    status.pop("ok", None)
    labor_changed = convention_changed = False
    errors = []
    try:
        labor_changed |= scan_boe(labor, status, today)
    except Exception as error:
        status["boe"] = {"ok": False, "message": f"{type(error).__name__}: {error}"}
        errors.append("BOE")
    try:
        a, b = scan_bop(labor, convention, status, today)
        labor_changed |= a
        convention_changed |= b
    except Exception as error:
        status["bopGranada"] = {"ok": False, "message": f"{type(error).__name__}: {error}"}
        errors.append("BOP de Granada")

    failures = status.get("consecutiveFailures", 0) + 1 if errors else 0
    if failures == FAILURES_BEFORE_ALERT:
        report.append(
            f"- **El robot lleva {failures} dias sin poder revisar:** {', '.join(errors)}. "
            "Puede que la web haya cambiado. Detalle en `data/labor_robot_status.json`."
        )
    status["consecutiveFailures"] = failures
    status["lastRun"] = utc_now()
    status["ok"] = not errors

    if labor_changed:
        labor["generatedAt"] = utc_now()
        write_json_if_changed(LABOR_FILE, labor)
    if convention_changed:
        write_json_if_changed(CONVENTION_FILE, convention)
    write_json_if_changed(STATUS_FILE, status)
    if report:
        REPORT_FILE.write_text(
            "El robot laboral ha encontrado esto:\n\n" + "\n".join(report)
            + "\n\nLa app de cada persona lo recibe en su siguiente sincronizacion (10:00 y 18:00).\n",
            encoding="utf-8",
        )
    print("Robot laboral terminado." + (" Incidencias: " + ", ".join(errors) if errors else " Todo correcto."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
