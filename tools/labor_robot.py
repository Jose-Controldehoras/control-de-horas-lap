#!/usr/bin/env python3
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
STATUS_FILE = ROOT / "data" / "labor_robot_status.json"
USER_AGENT = "ControlHorasLAP-LaborRobot/1.0 (+https://github.com/Jose-Controldehoras/control-de-horas-lap)"
BOE_SUMMARY_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{date}"
BOP_HOME_URL = "https://bop-admin.dipgra.es/publica/consulta-de-bops/"


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
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
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


def latest_smi(labor):
    values = labor.get("smi", [])
    return max(values, key=lambda item: (item.get("year", 0), item.get("effectiveFrom", ""))) if values else {}


def upsert_news(labor, item):
    news = labor.setdefault("news", [])
    news[:] = [old for old in news if old.get("id") != item.get("id")]
    news.append(item)
    news.sort(key=lambda old: old.get("publishedAt", ""), reverse=True)


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


def scan_boe(labor, status):
    today = dt.date.today()
    raw_last = status.get("lastBoeScan", "")
    try:
        last = dt.datetime.strptime(raw_last, "%Y%m%d").date()
    except Exception:
        last = today - dt.timedelta(days=14)
    if last.year < today.year:
        last = dt.date(today.year, 1, 1) - dt.timedelta(days=1)
    start = max(last + dt.timedelta(days=1), today - dt.timedelta(days=45))
    found = []
    day = start
    while day <= today:
        try:
            xml = fetch_bytes(BOE_SUMMARY_URL.format(date=day.strftime("%Y%m%d")), "application/xml")
            root = ET.fromstring(xml)
            for item in root.findall(".//item"):
                title = normalized(item.findtext("titulo", ""))
                if "salario minimo interprofesional para" not in title:
                    continue
                found.append(parse_boe_smi_document(item, day))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        day += dt.timedelta(days=1)

    changed = False
    for record in found:
        values = labor.setdefault("smi", [])
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
                f"{record['monthly']:.2f} EUR al mes, con efectos desde {record['effectiveFrom']}. "
                "La app actualiza automaticamente el calculo de vacaciones pagadas."
            ),
            "publishedAt": record["publishedAt"] + "T00:00:00Z",
            "effectiveFrom": record["effectiveFrom"],
            "url": record["officialUrl"],
        })
        changed = True
    status["lastBoeScan"] = today.strftime("%Y%m%d")
    period_text = (
        f"Sumarios revisados desde {start.isoformat()}"
        if start <= today
        else "No habia dias nuevos desde la ultima revision"
    )
    status["boe"] = {
        "ok": True,
        "message": f"{period_text}. {len(found)} cambios de SMI detectados.",
    }
    return changed


def pdf_text(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_rate_section(section):
    return {
        "extra": find_amount(section, r"VALOR HORA EXTRA\s+(\d+,\d+)"),
        "night22": find_amount(section, r"VALOR HORA NOCTURNA DE 22\.00 A 23\.00 H\s+(\d+,\d+)"),
        "nightRest": find_amount(section, r"VALOR HORA NOCTURNA RESTO DE CONCEPTOS\s+(\d+,\d+)"),
        "extraNight22": find_amount(section, r"VALOR HORA EXTRA \+ NOCTURNA DE 22\.00 A 23\.00 H\s+(\d+,\d+)"),
        "extraNightRest": find_amount(section, r"VALOR HORA EXTRA \+ NOCTURNA RESTO DE\s+CONCEPTOS\s+(\d+,\d+)"),
    }


def parse_bop_salary_table(text, publication_date, official_url):
    clean = re.sub(r"\s+", " ", text)
    upper = clean.upper()
    if "MANIPULADO Y ENVASADO DE FRUTAS" not in upper:
        return None
    if "MANIPULADORA Y MOZO DE ALMAC" not in upper or "JEFE DE LINEA" not in upper.replace("Í", "I"):
        return None
    ordinary = upper.find("VALOR HORA ORDINARIA")
    mozo_section_start = upper.find("VALOR HORA EXTRA, NOCTURNA Y FESTIVAS", ordinary)
    jefe_section_start = upper.find("VALOR HORA EXTRA, NOCTURNA Y FESTIVAS", mozo_section_start + 20)
    carencia_start = upper.find("PLUS CARENCIA", jefe_section_start)
    if min(ordinary, mozo_section_start, jefe_section_start, carencia_start) < 0:
        raise ValueError("Tabla salarial detectada, pero su formato no es completo")

    ordinary_text = clean[ordinary:mozo_section_start]
    mozo_section = clean[mozo_section_start:jefe_section_start]
    jefe_section = clean[jefe_section_start:carencia_start]
    carencia = clean[carencia_start:]
    mozo = parse_rate_section(mozo_section)
    jefe = parse_rate_section(jefe_section)
    mozo["normal"] = find_amount(ordinary_text, r"MANIPULADORA Y MOZO DE ALMAC[ÉE]N\s+(\d+,\d+)")
    jefe["normal"] = find_amount(ordinary_text, r"JEFA DE L[ÍI]NEA Y JEFE DE EQUIPO\s+(\d+,\d+)")
    mozo["carencia"] = find_amount(carencia, r"MANIPULADORA Y MOZO DE ALMAC[ÉE]N\s+(\d+,\d+)")
    jefe["carencia"] = find_amount(carencia, r"JEFA DE L[ÍI]NEA Y JEFE DE EQUIPO\s+(\d+,\d+)")

    effective_match = re.search(r"(?:A PARTIR DE|APLICACI[ÓO]N A PARTIR DE)\s+(\d{1,2})[\/\s]+(?:DE\s+)?ENERO(?:\s+DE|\/)(\d{4})", upper)
    if not effective_match:
        effective_match = re.search(r"1/1/(\d{4})", upper)
        year = int(effective_match.group(1)) if effective_match else publication_date.year
        effective = f"{year:04d}-01-01"
    else:
        effective = f"{int(effective_match.group(2)):04d}-01-{int(effective_match.group(1)):02d}"

    for rates in (mozo, jefe):
        if not all(0 < value < 100 for value in rates.values()):
            raise ValueError("Tabla salarial con importes fuera de rango")
    digest = hashlib.sha256(json.dumps({"mozo": mozo, "jefe": jefe}, sort_keys=True).encode()).hexdigest()[:10]
    return {
        "revision": f"frutas-granada-{publication_date.year}-{digest}",
        "effectiveFrom": effective,
        "publishedAt": publication_date.isoformat(),
        "reference": f"BOP Granada {publication_date.strftime('%d/%m/%Y')}",
        "officialUrl": official_url,
        "summary": "Tabla salarial oficial detectada y validada por el robot.",
        "rates": {"mozo": mozo, "jefe": jefe},
    }


def scan_bop_granada(labor, status):
    page = fetch_text(BOP_HOME_URL)
    match = re.search(r'href="([^"]*/Documentos-BOPs-en-PDF/[^"]+\.pdf[^"]*)"', page, re.I)
    if not match:
        raise ValueError("No se encontro el boletin diario de Granada")
    official_url = urllib.parse.urljoin(BOP_HOME_URL, html.unescape(match.group(1)))
    date_match = re.search(r"bop-(\d{2})_(\d{2})_(\d{4})", official_url)
    publication_date = (
        dt.date(int(date_match.group(3)), int(date_match.group(2)), int(date_match.group(1)))
        if date_match else dt.date.today()
    )
    table = parse_bop_salary_table(pdf_text(fetch_bytes(official_url, "application/pdf")), publication_date, official_url)
    changed = False
    if table:
        current = labor.setdefault("salaryTables", {}).get("frutas_granada", {})
        if current.get("revision") != table["revision"]:
            labor["salaryTables"]["frutas_granada"] = table
            mozo_total = table["rates"]["mozo"]["normal"] + table["rates"]["mozo"]["carencia"]
            jefe_total = table["rates"]["jefe"]["normal"] + table["rates"]["jefe"]["carencia"]
            upsert_news(labor, {
                "id": f"labor-convenio-frutas-granada-{table['effectiveFrom'][:4]}",
                "kind": "official_update",
                "source": "Convenio oficial de Granada",
                "title": "Nueva tabla salarial oficial disponible",
                "summary": (
                    f"La tabla fija {mozo_total:.2f} EUR/h para mozo y {jefe_total:.2f} EUR/h "
                    f"para jefe de linea, con efectos desde {table['effectiveFrom']}. "
                    "La app pedira confirmacion antes de recalcular meses anteriores."
                ),
                "publishedAt": table["publishedAt"] + "T00:00:00Z",
                "effectiveFrom": table["effectiveFrom"],
                "url": table["officialUrl"],
            })
            changed = True
    status["bopGranada"] = {
        "ok": True,
        "message": (
            f"BOP de {publication_date.isoformat()} revisado. "
            + ("Tabla salarial completa detectada." if table else "Sin tabla salarial nueva.")
        ),
        "officialUrl": official_url,
    }
    return changed


def main():
    labor = load_json(LABOR_FILE, {"version": 1, "smi": [], "salaryTables": {}, "news": []})
    status = load_json(STATUS_FILE, {})
    changed = False
    errors = []
    try:
        changed |= scan_boe(labor, status)
    except Exception as error:
        status["boe"] = {"ok": False, "message": f"Revision BOE controlada: {type(error).__name__}: {error}"}
        errors.append("BOE")
    try:
        changed |= scan_bop_granada(labor, status)
    except Exception as error:
        status["bopGranada"] = {"ok": False, "message": f"Revision BOP controlada: {type(error).__name__}: {error}"}
        errors.append("BOP Granada")

    status["lastRun"] = utc_now()
    status["ok"] = len(errors) == 0
    if changed:
        labor["generatedAt"] = utc_now()
        write_json_if_changed(LABOR_FILE, labor)
    write_json_if_changed(STATUS_FILE, status)
    print("Robot laboral terminado." + (" Fuentes con incidencias: " + ", ".join(errors) if errors else " Todo correcto."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
