"""
Baja el catálogo de Fever desde la API de Impact y regenera datos/fever/*.json.
Lo corre GitHub Actions los jueves (ver .github/workflows/fever.yml).
Necesita los secretos IMPACT_SID e IMPACT_TOKEN (Impact → Settings → API).
"""
import json, os, sys, time
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fever_catalogo import procesar

SID, TOKEN = os.environ.get("IMPACT_SID", ""), os.environ.get("IMPACT_TOKEN", "")
BASE = f"https://api.impact.com/Mediapartners/{SID}"
CATALOGO = os.environ.get("FEVER_CATALOGO", "15532")  # "2024 Impact Catalog"
COLUMNAS = ["Country", "State", "City", "Venue", "Location", "Coordinates", "SKU", "Plan Name", "Affiliate URL", "Fever URL",
            "Description", "Start date", "End date", "Sessions", "Minimum Price", "Currency", "Ticket prices",
            "Category", "Subcategory", "Additional Notes", "Photo 1", "Photo 2"]

def get(url, **params):
    for intento in range(4):
        r = requests.get(url, params=params, auth=(SID, TOKEN), headers={"Accept": "application/json"}, timeout=120)
        if r.status_code == 429:
            time.sleep(30); continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Impact no respondió")

def aplanar(item):
    """Ítem de la API de Impact → columnas del CSV de Fever (Impact reusa campos estándar para los datos de Fever)."""
    g = lambda k: item.get(k) if item.get(k) not in (None, []) else ""
    labels = item.get("Labels") or []
    return {
        "Country": g("Text1") or g("Size"), "City": g("Text2"), "Venue": g("Material"), "Location": g("ShippingLabel"),
        "Coordinates": g("Pattern"), "SKU": g("CatalogItemId"), "Plan Name": g("Name"), "Affiliate URL": g("Url"),
        "Fever URL": (item.get("Colors") or [""])[0], "Description": g("Description"), "Sessions": g("Manufacturer"),
        "Minimum Price": g("CurrentPrice"), "Currency": g("Currency"),
        "Ticket prices": ", ".join(x.strip() for x in labels if isinstance(x, str)),
        "Category": g("Category"), "Subcategory": g("SubCategory"), "Additional Notes": g("Text3"), "Photo 1": g("ImageUrl"),
    }

def desde_archivo():
    """Intenta bajar el archivo CSV del catálogo (mismo formato que la descarga manual)."""
    import csv, gzip, io
    try:
        data = get(f"{BASE}/Catalogs/{CATALOGO}/Files")
    except Exception as e:
        print("Files no disponible:", e); return None
    print("Files:", json.dumps(data, ensure_ascii=False)[:1500])
    urls = []
    def buscar(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, str) and v.startswith("http") and any(t in v.lower() for t in (".csv", ".gz", "download", "file")):
                    urls.append(v)
                else:
                    buscar(v)
        elif isinstance(x, list):
            for y in x: buscar(y)
    buscar(data)
    for u in urls:
        try:
            r = requests.get(u, auth=(SID, TOKEN), timeout=300)
            if r.status_code != 200:
                r = requests.get(u, timeout=300)  # algunos links ya vienen firmados
            r.raise_for_status()
            crudo = r.content
            if crudo[:2] == b"\x1f\x8b":
                crudo = gzip.decompress(crudo)
            texto = crudo.decode("utf-8", errors="replace")
            filas = list(csv.DictReader(io.StringIO(texto)))
            if filas and "City" in filas[0] and "Sessions" in filas[0]:
                print(f"Archivo OK: {len(filas)} filas desde {u.split('?')[0]}")
                return filas
            print("Archivo sin las columnas esperadas:", list(filas[0].keys())[:30] if filas else "vacío")
        except Exception as e:
            print("No pude bajar", u.split("?")[0], e)
    return None

CRUDOS = []

def main():
    if not SID or not TOKEN:
        print("Faltan IMPACT_SID / IMPACT_TOKEN"); return
    filas = desde_archivo()
    if filas:
        print("Planes guardados:", procesar(filas)); return
    filas, pagina, url, params = [], 1, f"{BASE}/Catalogs/{CATALOGO}/Items", {"PageSize": 1000}
    while url:
        try:
            data = get(url, **params)
        except Exception as e:
            print(f"Corte en página {pagina}: {e}"); break
        items = data.get("Items") or data.get("CatalogItems") or []
        if pagina == 1:
            print("Meta:", {k: v for k, v in data.items() if k.startswith("@")})
        filas += [aplanar(i) for i in items]
        CRUDOS.extend(items)
        sig = data.get("@nextpageuri") or ""
        if not items or not sig:
            break
        url, params, pagina = "https://api.impact.com" + sig, {}, pagina + 1
    print(f"Total: {len(filas)} ítems")
    print("Planes guardados:", procesar(filas))

if __name__ == "__main__":
    # todo lo que se imprime queda también en datos/fever/_registro.txt (para revisar sin entrar a GitHub)
    import builtins, traceback
    registro = []
    _print = builtins.print
    def print(*a, **k):
        msg = " ".join(str(x) for x in a)
        for secreto in (SID, TOKEN):
            if secreto:
                msg = msg.replace(secreto, "***")
        registro.append(msg); _print(msg, **k)
    builtins.print = print
    try:
        main()
    except Exception:
        print(traceback.format_exc())
    os.makedirs("datos/fever", exist_ok=True)
    with open("datos/fever/_registro.txt", "w", encoding="utf-8") as fh:
        fh.write(time.strftime("%Y-%m-%d %H:%M UTC\n", time.gmtime()) + "\n".join(registro)[:20000])
