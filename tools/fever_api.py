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
    """Lleva un ítem de la API al formato de columnas del CSV (los nombres pueden venir con o sin espacios)."""
    plano = {}
    def meter(k, v):
        if isinstance(v, (str, int, float)) and v != "":
            plano[str(k)] = str(v)
    for k, v in item.items():
        if isinstance(v, list):
            for x in v:  # atributos personalizados: [{"Name": "City", "Value": "Madrid"}, ...]
                if isinstance(x, dict) and ("Name" in x or "Key" in x):
                    meter(x.get("Name") or x.get("Key"), x.get("Value"))
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                meter(k2, v2)
        else:
            meter(k, v)
    norm = {k.replace(" ", "").replace("_", "").lower(): v for k, v in plano.items()}
    fila = {c: norm.get(c.replace(" ", "").lower(), "") for c in COLUMNAS}
    # equivalencias con los campos estándar de Impact
    fila["Plan Name"] = fila["Plan Name"] or norm.get("name", "")
    fila["Affiliate URL"] = fila["Affiliate URL"] or norm.get("url", "") or norm.get("trackingurl", "")
    fila["Photo 1"] = fila["Photo 1"] or norm.get("imageurl", "")
    fila["SKU"] = fila["SKU"] or norm.get("catalogitemid", "") or norm.get("id", "")
    fila["Minimum Price"] = fila["Minimum Price"] or norm.get("currentprice", "")
    fila["Currency"] = fila["Currency"] or norm.get("currency", "")
    return fila

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
            if items:
                print("Claves:", list(items[0].keys()))
                print("Ejemplo de ítem:", json.dumps(items[0], ensure_ascii=False)[:9000])
        filas += [aplanar(i) for i in items]
        print(f"Página {pagina}: {len(items)} ítems")
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
