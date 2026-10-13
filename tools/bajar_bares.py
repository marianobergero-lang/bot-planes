"""
Descarga los bares de OpenStreetMap para las ciudades principales y los guarda en datos/bares/<ciudad>.json.
Lo corre GitHub Actions una vez por semana (ver .github/workflows/bares.yml), así la web los lee
directo y no depende de que OpenStreetMap responda rápido.
Uso manual: python tools/bajar_bares.py [ciudad1 ciudad2 ...]
"""
import json, os, re, sys, time
import requests

CIUDADES = {
    "barcelona": (41.3874, 2.1686), "madrid": (40.4168, -3.7038), "ibiza": (38.9067, 1.4206), "valencia": (39.4699, -0.3763),
    "mallorca": (39.5696, 2.6502), "berlin": (52.5200, 13.4050), "london": (51.5074, -0.1278), "amsterdam": (52.3676, 4.9041),
    "paris": (48.8566, 2.3522), "lisbon": (38.7223, -9.1393), "porto": (41.1579, -8.6291), "buenos aires": (-34.6037, -58.3816),
    "rio de janeiro": (-22.9068, -43.1729), "new york": (40.7128, -74.0060), "mexico city": (19.4326, -99.1332),
}
RADIO = 8000  # metros alrededor del centro
SERVIDORES = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
UA = {"User-Agent": "LetsPartIA/1.0 (stepwiseconsulting.es)"}

RE_VINO = re.compile(r"\bvin(o|os|s|oteca|acoteca)?\b|wine|bodega|celler|vermut|vermout", re.I)
RE_CERVEZA = re.compile(r"cervec|cerves|beer|brew|craft|pub|taproom|birra", re.I)
RE_COCTEL = re.compile(r"cocktail|c[oó]ctel|gin\b|speakeasy|mixolog", re.I)
RE_HIFI = re.compile(r"hi-?fi|listening|vinyl|vinilo|vinil|record bar|audiophile|soundsystem|sound system|disc bar|kissa", re.I)

def tipos(t):
    n = t.get("name", "")
    out = []
    if t.get("cocktails") == "yes" or t.get("drink:cocktail") in ("yes", "served") or RE_COCTEL.search(n): out.append("cocteles")
    if t.get("drink:wine") in ("yes", "served") or t.get("bar") == "wine" or RE_VINO.search(n): out.append("vinos")
    if t.get("amenity") in ("pub", "biergarten") or t.get("craft_beer") == "yes" or t.get("drink:craft_beer") in ("yes", "served") or RE_CERVEZA.search(n): out.append("cervezas")
    if t.get("outdoor_seating") == "yes" or t.get("amenity") == "biergarten": out.append("terraza")
    if t.get("live_music") == "yes": out.append("musica")
    if RE_HIFI.search(n) or RE_HIFI.search(t.get("description", "")): out.append("hifi")
    return out

def bajar(ciudad):
    lat, lng = CIUDADES[ciudad]
    q = f"""[out:json][timeout:120];
    (node["amenity"~"^(bar|pub|biergarten)$"]["name"](around:{RADIO},{lat},{lng});
     way["amenity"~"^(bar|pub|biergarten)$"]["name"](around:{RADIO},{lat},{lng}););
    out center tags;"""
    for url in SERVIDORES:
        for intento in range(2):
            try:
                r = requests.post(url, data={"data": q}, headers=UA, timeout=180)
                r.raise_for_status()
                return r.json().get("elements") or []
            except Exception as e:
                print(f"  {url} intento {intento + 1}: {e}")
                time.sleep(15)
    return None

def compactar(el):
    t = el.get("tags") or {}
    la = el.get("lat") or (el.get("center") or {}).get("lat")
    lo = el.get("lon") or (el.get("center") or {}).get("lon")
    if la is None or not t.get("name"):
        return None
    return {
        "id": f"osm-{el.get('type')}-{el.get('id')}",
        "n": t["name"],
        "la": round(la, 6), "lo": round(lo, 6),
        "c": {"pub": "Pub", "biergarten": "Cervecería al aire libre"}.get(t.get("amenity"), "Bar"),
        "t": tipos(t),
        "d": " ".join(x for x in [t.get("addr:street"), t.get("addr:housenumber")] if x),
        "b": t.get("addr:suburb") or t.get("addr:district") or "",
        "h": t.get("opening_hours", ""),
        "w": t.get("website") or t.get("contact:website") or "",
        "p": t.get("phone") or t.get("contact:phone") or "",
    }

def main():
    ciudades = sys.argv[1:] or list(CIUDADES)
    os.makedirs("datos/bares", exist_ok=True)
    for c in ciudades:
        print(f"Bajando {c}…")
        els = bajar(c)
        if els is None:
            print(f"  No se pudo bajar {c}; dejo el archivo anterior si existe")
            continue
        bares = [b for b in (compactar(e) for e in els) if b]
        nombre = c.replace(" ", "-")
        with open(f"datos/bares/{nombre}.json", "w", encoding="utf-8") as f:
            json.dump({"ciudad": c, "centro": CIUDADES[c], "radio": RADIO, "actualizado": time.strftime("%Y-%m-%d"), "bares": bares},
                      f, ensure_ascii=False, separators=(",", ":"))
        print(f"  {len(bares)} bares guardados")
        time.sleep(10)  # respetar a los servidores de OpenStreetMap

if __name__ == "__main__":
    main()
