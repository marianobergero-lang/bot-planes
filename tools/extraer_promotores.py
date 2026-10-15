"""Extrae las fiestas de RA de una ciudad en un rango de fechas, con su promotora.
Uso: python tools/extraer_promotores.py [area_id] [desde] [hasta]  (por defecto Barcelona, el próximo finde)"""
import csv, json, sys, time, datetime as dt
import requests

URL = "https://ra.co/graphql"
H = {"Content-Type": "application/json", "Origin": "https://ra.co", "Referer": "https://ra.co/events",
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
Q = """query($filters: FilterInputDtoInput, $pageSize: Int, $page: Int) {
  eventListings(filters: $filters, pageSize: $pageSize, page: $page,
    sort: { listingDate: { priority: 1, order: ASCENDING } }) {
    data { listingDate event { id title date startTime contentUrl attending
      venue { name address }
      promoters { id name contentUrl website instagram facebook followerCount upcomingEventsCount area { name } } } }
    totalResults } }"""

Q_MIN = Q.replace("website instagram facebook followerCount upcomingEventsCount area { name } ", "")
LOG = []
hoy = dt.date.today()
vie = hoy + dt.timedelta(days=(4 - hoy.weekday()) % 7)
area = int(sys.argv[1]) if len(sys.argv) > 1 else 20
desde = sys.argv[2] if len(sys.argv) > 2 else vie.isoformat()
hasta = sys.argv[3] if len(sys.argv) > 3 else (vie + dt.timedelta(days=2)).isoformat()

filas, page = [], 1
while True:
    v = {"filters": {"areas": {"eq": area}, "listingDate": {"gte": desde, "lte": hasta}}, "pageSize": 100, "page": page}
    d = None
    for q in (Q, Q_MIN):
        r = requests.post(URL, headers=H, json={"query": q, "variables": v}, timeout=30)
        LOG.append(f"HTTP {r.status_code} ({'completa' if q is Q else 'mínima'}): {r.text[:600]}" if r.status_code != 200 or '"errors"' in r.text[:2000] else f"HTTP 200 ok ({'completa' if q is Q else 'mínima'})")
        if r.status_code == 200 and r.json().get("data", {}).get("eventListings"):
            d = r.json(); break
    if not d: break
    el = d["data"]["eventListings"]
    for it in el["data"]:
        e = it["event"]
        proms = e.get("promoters") or [{}]
        for p in proms:
            filas.append({
                "fecha": (e.get("date") or it["listingDate"])[:10],
                "hora": (e.get("startTime") or "")[11:16],
                "fiesta": e.get("title"), "lugar": (e.get("venue") or {}).get("name"),
                "direccion": (e.get("venue") or {}).get("address"),
                "interesados": e.get("attending"),
                "url_fiesta": "https://ra.co" + (e.get("contentUrl") or ""),
                "promotor": p.get("name") or "", "promotor_ra": ("https://ra.co" + p["contentUrl"]) if p.get("contentUrl") else "",
                "web": p.get("website") or "", "instagram": p.get("instagram") or "", "facebook": p.get("facebook") or "",
                "seguidores_ra": p.get("followerCount"), "proximos_eventos": p.get("upcomingEventsCount"),
                "zona_promotor": (p.get("area") or {}).get("name") or "",
            })
    print(f"página {page}: {len(el['data'])} de {el['totalResults']}")
    if page * 100 >= el["totalResults"] or not el["data"]: break
    page += 1; time.sleep(1)

# sin duplicados (RA repite el evento por cada noche listada)
vistos, unicas = set(), []
for f in filas:
    k = (f["url_fiesta"], f["promotor"])
    if k not in vistos: vistos.add(k); unicas.append(f)
sal = f"extracciones/promotores_{area}_{desde}_{hasta}.csv"
with open(sal, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(unicas[0].keys()) if unicas else ["fiesta"]); w.writeheader(); w.writerows(unicas)
LOG.append(f"{len(unicas)} filas → {sal}")
open("extracciones/log.txt", "w").write("\n".join(LOG))
print("\n".join(LOG))
