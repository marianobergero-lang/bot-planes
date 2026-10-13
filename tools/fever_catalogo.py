"""
Convierte el catálogo de Fever (Impact, "2024 Impact Catalog", CSV o CSV.gz) en archivos por ciudad:
datos/fever/<ciudad>.json — solo planes con fechas en los próximos 120 días y de categorías que mostramos.
Uso: python tools/fever_catalogo.py ruta/al/catalogo.csv.gz
"""
import csv, gzip, json, os, re, sys, unicodedata
from datetime import datetime, timedelta

# nuestra ciudad → nombres de ciudad en el catálogo de Fever
CIUDADES = {
    "barcelona": ["Barcelona"], "madrid": ["Madrid"], "ibiza": ["Ibiza"], "valencia": ["Valencia"],
    "berlin": ["Berlin"], "london": ["London"], "amsterdam": ["Amsterdam"], "paris": ["Paris"], "lisbon": ["Lisbon"],
    "porto": ["Porto"], "rome": ["Rome"], "milan": ["Milan"], "munich": ["Munich"], "hamburg": ["Hamburg"],
    "vienna": ["Vienna"], "brussels": ["Brussels"], "prague": ["Prague"], "budapest": ["Budapest"], "stockholm": ["Stockholm"],
    "buenos aires": ["Buenos Aires"], "rio de janeiro": ["Rio de Janeiro"], "sao paulo": ["São Paulo"],
    "mexico city": ["Mexico City"], "bogota": ["Bogotá"], "santiago": ["Santiago"], "new york": ["New York"],
    "los angeles": ["Los Angeles"], "miami": ["Miami"], "chicago": ["Chicago"], "toronto": ["Toronto"],
    "montreal": ["Montreal"], "tokyo": ["Tokyo"], "sydney": ["Sydney"], "melbourne": ["Melbourne"], "turin": ["Turin"],
}
DIAS = 120          # ventana de fechas que guardamos
MAX_FECHAS = 60     # tope de días por plan (algunos tienen funciones todos los días)

CLUB = {"Nightlife & Party", "Night Clubs", "Themed Parties", "Nightlife and Clubs", "Parties"}
CONCIERTO = {"Concerts", "Music Events", "Candlelight Concerts", "Classical Concerts", "Concerts & Music Festivals",
             "Festivals", "Jazz", "Live Music", "Music Festivals"}
TEATRO = {"Theater", "Theatre", "Live Shows", "Comedy & Shows", "Stand-Up Comedy", "Musicals", "Magic Shows", "Dance", "Opera"}
NO_GENERO = {"Gift Cards", "NSW Parents", "Parent's Day", "Father's Day", "Mother's Day", "Festive Season", "Valentine's Day",
             "Black Friday {year}", "On A Budget", "Plans For Two", "Fever Originals", "Other Experiences", "Other", "Family"}

def categoria(subs):
    s = set(subs)
    if s & CLUB and "Family" not in s:
        return "clubs"
    if s & CONCIERTO or any(x.startswith("Candlelight") for x in s):
        return "conciertos"
    if s & TEATRO:
        return "teatro"
    return None

def moneda(r):
    m = re.search(r"\b([A-Z]{3})\b", r.get("Ticket prices") or "")  # la columna Currency a veces no coincide con el precio
    return m.group(1) if m else (r.get("Currency") or "EUR").strip() or "EUR"

def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def coords(txt):
    m = re.match(r"\(\s*(-?\d+(?:\.\d+)?)\s*;\s*(-?\d+(?:\.\d+)?)\s*\)", txt or "")
    return (round(float(m.group(1)), 5), round(float(m.group(2)), 5)) if m else (None, None)

def main(ruta):
    abre = gzip.open if ruta.endswith(".gz") else open
    with abre(ruta, "rt", encoding="utf-8", errors="replace") as fh:
        filas = list(csv.DictReader(fh))
    procesar(filas)

def procesar(filas):
    """filas = dicts con las columnas del CSV de Fever. Devuelve la cantidad total de planes guardados."""
    hoy = datetime.now().date()
    lim = hoy + timedelta(days=DIAS)
    inverso = {n: k for k, ns in CIUDADES.items() for n in ns}
    salida = {k: [] for k in CIUDADES}
    for r in filas:
        ciudad = inverso.get((r.get("City") or "").strip())
        if not ciudad:
            continue
        subs = [s.strip() for s in (r.get("Subcategory") or "").split(",") if s.strip()]
        cat = categoria(subs)
        if not cat:
            continue
        fechas = {}
        for s in (r.get("Sessions") or "").split(","):
            try:
                d = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if hoy <= d.date() <= lim:
                k = d.strftime("%Y-%m-%d")
                if k not in fechas or d.strftime("%H:%M") < fechas[k]:
                    fechas[k] = d.strftime("%H:%M")
        if not fechas:
            continue
        la, lo = coords(r.get("Coordinates"))
        tier = (r.get("Category") or "")
        generos = [s for s in subs if s not in NO_GENERO and not s.startswith("Candlelight Concerts in ")][:3]
        salida[ciudad].append({
            "id": r.get("SKU"),
            "n": (r.get("Plan Name") or "").strip(),
            "v": (r.get("Venue") or "").strip(),
            "d": (r.get("Location") or "").strip(),
            "la": la, "lo": lo,
            "u": r.get("Affiliate URL") or r.get("Fever URL"),
            "img": r.get("Photo 1") or None,
            "p": num(r.get("Minimum Price")), "m": moneda(r),
            "c": cat, "g": generos,
            "cl": any(s.startswith("Candlelight") for s in subs),
            "top": "top" in (r.get("Additional Notes") or "").lower(),
            "com": not tier.startswith("Tier 4"),   # Tier 4 = sin comisión
            "f": sorted(fechas.items())[:MAX_FECHAS],
        })
    total = sum(len(v) for v in salida.values())
    if total < 100:
        print(f"Solo {total} planes: algo salió mal, dejo los archivos anteriores")
        return total
    os.makedirs("datos/fever", exist_ok=True)
    for ciudad, planes in salida.items():
        with open(f"datos/fever/{ciudad.replace(' ', '-')}.json", "w", encoding="utf-8") as fh:
            json.dump({"ciudad": ciudad, "actualizado": hoy.isoformat(), "planes": planes}, fh, ensure_ascii=False, separators=(",", ":"))
        print(f"{ciudad}: {len(planes)} planes")
    return sum(len(v) for v in salida.values())

if __name__ == "__main__":
    main(sys.argv[1])
