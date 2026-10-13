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

def main():
    if not SID or not TOKEN:
        print("Faltan IMPACT_SID / IMPACT_TOKEN"); return
    filas, pagina = [], 1
    while True:
        data = get(f"{BASE}/Catalogs/{CATALOGO}/Items", PageSize=1000, Page=pagina)
        items = data.get("Items") or data.get("CatalogItems") or []
        if pagina == 1 and items:  # para revisar el formato en el log de GitHub
            print("Ejemplo de ítem:", json.dumps(items[0], ensure_ascii=False)[:2500])
        filas += [aplanar(i) for i in items]
        print(f"Página {pagina}: {len(items)} ítems")
        total_paginas = int(data.get("@numpages") or data.get("NumPages") or 0)
        if not items or (total_paginas and pagina >= total_paginas):
            break
        pagina += 1
    print(f"Total: {len(filas)} ítems")
    print("Planes guardados:", procesar(filas))

if __name__ == "__main__":
    main()
