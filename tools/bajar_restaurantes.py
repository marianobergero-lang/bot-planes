"""
Descarga los restaurantes de OpenStreetMap y los guarda en datos/restaurantes/<ciudad>.json.
Lo corre GitHub Actions una vez por semana, igual que los bares.
Uso manual: python tools/bajar_restaurantes.py [ciudad1 ciudad2 ...]
"""
import json, os, re, sys, time
import requests
from bajar_bares import CIUDADES, SERVIDORES, UA

RADIO = 6000  # metros alrededor del centro (más chico que bares: hay muchos más restaurantes)

# Cocina de OpenStreetMap → filtro de la app
COCINAS = {
    "tapas": ["tapas", "spanish", "catalan", "basque", "galician", "andalusian", "regional", "paella"],
    "italiana": ["italian", "pasta", "italian_pizza"],
    "pizza": ["pizza"],
    "japonesa": ["japanese", "sushi", "ramen", "izakaya", "udon"],
    "asiatica": ["chinese", "thai", "vietnamese", "asian", "korean", "dim_sum", "noodle", "malaysian", "indonesian", "cantonese", "sichuan"],
    "mexicana": ["mexican", "tex-mex", "tacos", "burrito"],
    "hamburguesas": ["burger", "american"],
    "india": ["indian", "pakistani", "nepalese", "curry", "bangladeshi"],
    "parrilla": ["steak_house", "argentinian", "grill", "barbecue", "brazilian", "churrascaria", "asado"],
    "mariscos": ["seafood", "fish", "fish_and_chips"],
    "mediterranea": ["mediterranean", "greek", "lebanese", "turkish", "middle_eastern", "arab", "moroccan", "kebab", "falafel"],
    "latina": ["peruvian", "latin_american", "venezuelan", "colombian", "cuban", "arepa", "ceviche"],
    "francesa": ["french", "bistro", "creperie", "crepe"],
}
ETIQUETA = {"tapas": "Tapas / Española", "italiana": "Italiana", "pizza": "Pizza", "japonesa": "Japonesa",
            "asiatica": "Asiática", "mexicana": "Mexicana", "hamburguesas": "Hamburguesas", "india": "India",
            "parrilla": "Parrilla / Carnes", "mariscos": "Pescados y mariscos", "mediterranea": "Mediterránea",
            "latina": "Latinoamericana", "francesa": "Francesa"}
RE_NOMBRE = {
    "tapas": re.compile(r"tapas|tasca|taberna|bodega|mes[oó]n|txoko|pintxo", re.I),
    "pizza": re.compile(r"pizz", re.I),
    "japonesa": re.compile(r"sushi|ramen|izakaya", re.I),
    "hamburguesas": re.compile(r"burger", re.I),
    "parrilla": re.compile(r"parrill|asador|grill|steak", re.I),
}

def cocinas(t):
    raw = [c.strip().lower() for c in re.split(r"[;,]", t.get("cuisine", "")) if c.strip()]
    out = [k for k, vals in COCINAS.items() if any(c in vals for c in raw)]
    for k, rx in RE_NOMBRE.items():
        if k not in out and rx.search(t.get("name", "")):
            out.append(k)
    return out

def flags(t):
    out = []
    if t.get("outdoor_seating") == "yes": out.append("terraza")
    if t.get("diet:vegetarian") in ("yes", "only") or t.get("diet:vegan") in ("yes", "only"): out.append("veg")
    if t.get("takeaway") in ("yes", "only"): out.append("llevar")
    return out

def bajar(ciudad):
    lat, lng = CIUDADES[ciudad]
    q = f"""[out:json][timeout:180];
    (node["amenity"="restaurant"]["name"](around:{RADIO},{lat},{lng});
     way["amenity"="restaurant"]["name"](around:{RADIO},{lat},{lng}););
    out center tags;"""
    for url in SERVIDORES:
        for intento in range(2):
            try:
                r = requests.post(url, data={"data": q}, headers=UA, timeout=240)
                r.raise_for_status()
                return r.json().get("elements") or []
            except Exception as e:
                print(f"  {url} intento {intento + 1}: {e}")
                time.sleep(20)
    return None

def compactar(el):
    t = el.get("tags") or {}
    la = el.get("lat") or (el.get("center") or {}).get("lat")
    lo = el.get("lon") or (el.get("center") or {}).get("lon")
    if la is None or not t.get("name"):
        return None
    k, f = cocinas(t), flags(t)
    web = t.get("website") or t.get("contact:website") or ""
    hor = t.get("opening_hours", "")
    if not (k or f or web or hor):  # sin ningún dato útil, no suma
        return None
    r = {"id": f"{el.get('type', 'n')[0]}{el.get('id')}", "n": t["name"], "la": round(la, 5), "lo": round(lo, 5)}
    if k: r["k"] = k
    if f: r["t"] = f
    d = " ".join(x for x in [t.get("addr:street"), t.get("addr:housenumber")] if x)
    if d: r["d"] = d
    b = t.get("addr:suburb") or t.get("addr:district") or ""
    if b: r["b"] = b
    if hor: r["h"] = hor
    if web: r["w"] = web
    p = t.get("phone") or t.get("contact:phone") or ""
    if p: r["p"] = p
    return r

def main():
    ciudades = sys.argv[1:] or list(CIUDADES)
    os.makedirs("datos/restaurantes", exist_ok=True)
    for c in ciudades:
        print(f"Bajando {c}…")
        els = bajar(c)
        if els is None:
            print(f"  No se pudo bajar {c}; dejo el archivo anterior si existe")
            continue
        rs = [x for x in (compactar(e) for e in els) if x]
        with open(f"datos/restaurantes/{c.replace(' ', '-')}.json", "w", encoding="utf-8") as f:
            json.dump({"ciudad": c, "centro": CIUDADES[c], "radio": RADIO, "actualizado": time.strftime("%Y-%m-%d"),
                       "etiquetas": ETIQUETA, "restaurantes": rs}, f, ensure_ascii=False, separators=(",", ":"))
        print(f"  {len(rs)} restaurantes guardados (de {len(els)})")
        time.sleep(15)  # respetar a los servidores de OpenStreetMap

if __name__ == "__main__":
    main()
