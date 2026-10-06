from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

# ── CONFIG ────────────────────────────────────────────────────────────────────
SCRAPER_API_KEY = "00aa3ccf3ef48efc379726eacdccf27f"
RA_GRAPHQL_URL  = "https://ra.co/graphql"

AREAS_RA = {
    "buenos aires": 385, "berlin": 17, "barcelona": 7,
    "london": 13, "amsterdam": 9, "madrid": 133,
    "new york": 8, "paris": 15, "ibiza": 29,
    "mexico city": 411, "bogota": 473,
}

RA_QUERY = """
query GET_EVENTS($filters: FilterInputDtoInput, $pageSize: Int) {
  eventListings(
    filters: $filters
    pageSize: $pageSize
    page: 1
    sort: { listingDate: { priority: 1, order: ASCENDING } }
  ) {
    data {
      id
      listingDate
      event {
        id
        title
        date
        startTime
        endTime
        contentUrl
        cost
        venue { name address area { name } }
        artists { displayName }
        pick { blurb }
        attending
      }
    }
    totalResults
  }
}
"""

# ── HELPERS ───────────────────────────────────────────────────────────────────
def scraper_post(url, json_payload):
    """Hace un POST pasando por ScraperAPI para evitar bloqueos."""
    import json
    scraper_url = "https://api.scraperapi.com/"
    params = {
        "api_key": SCRAPER_API_KEY,
        "url": url,
        "keep_headers": "true",
    }
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://ra.co",
        "Referer": "https://ra.co/events",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    resp = requests.post(
        scraper_url,
        params=params,
        headers=headers,
        data=json.dumps(json_payload),
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()

def finde_proximo():
    hoy = datetime.now()
    dias = (4 - hoy.weekday()) % 7 or 7
    viernes = hoy + timedelta(days=dias)
    domingo = viernes + timedelta(days=2)
    return viernes.strftime("%Y-%m-%d"), domingo.strftime("%Y-%m-%d")

# ── FUENTE: RESIDENT ADVISOR ──────────────────────────────────────────────────
def buscar_ra(ciudad, desde, hasta, max_ev=10):
    area = AREAS_RA.get(ciudad)
    if not area:
        return [], 0

    payload = {
        "operationName": "GET_EVENTS",
        "variables": {
            "filters": {
                "areas": {"eq": area},
                "listingDate": {
                    "gte": f"{desde}T00:00:00.000Z",
                    "lte": f"{hasta}T23:59:59.000Z",
                }
            },
            "pageSize": max_ev * 2
        },
        "query": RA_QUERY
    }

    try:
        data = scraper_post(RA_GRAPHQL_URL, payload)
        listings = data.get("data", {}).get("eventListings", {}).get("data", [])
        total    = data.get("data", {}).get("eventListings", {}).get("totalResults", 0)

        eventos = []
        for item in listings:
            ev = item.get("event")
            if not ev:
                continue
            venue = ev.get("venue") or {}
            cost  = (ev.get("cost") or "").strip()
            eventos.append({
                "titulo":     ev.get("title", ""),
                "fecha":      (ev.get("date") or "")[:10],
                "hora":       (ev.get("startTime") or "")[:5],
                "venue":      venue.get("name", ""),
                "direccion":  venue.get("address", ""),
                "artistas":   [a["displayName"] for a in ev.get("artists", [])],
                "precio":     cost or "No especificado",
                "gratis":     cost == "" or cost.lower() in ["free", "gratis", "0"],
                "asistentes": ev.get("attending", 0),
                "destacado":  (ev.get("pick") or {}).get("blurb", ""),
                "url":        f"https://ra.co{ev.get('contentUrl', '')}",
                "fuente":     "Resident Advisor",
            })
        return eventos, total
    except Exception as e:
        print(f"[RA ERROR] {e}")
        return [], 0

# ── ENDPOINTS ─────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return jsonify({
        "status":  "ok",
        "mensaje": "Bot de Planes API — con ScraperAPI para RA",
        "fuentes": ["resident_advisor"]
    })

@app.route("/eventos")
def eventos():
    ciudad   = request.args.get("ciudad", "berlin").lower().strip()
    desde    = request.args.get("desde")
    hasta    = request.args.get("hasta")
    hora_min = request.args.get("hora_min")
    gratis   = request.args.get("gratis")
    max_ev   = int(request.args.get("max", 10))

    if not desde or not hasta:
        desde, hasta = finde_proximo()

    if ciudad not in AREAS_RA:
        return jsonify({
            "error": f"Ciudad '{ciudad}' no encontrada",
            "disponibles": list(AREAS_RA.keys())
        }), 400

    eventos_ra, total_ra = buscar_ra(ciudad, desde, hasta, max_ev)

    # Filtros
    if hora_min:
        eventos_ra = [e for e in eventos_ra
                      if not e["hora"] or e["hora"] >= hora_min or e["hora"] < "08:00"]
    if gratis == "true":
        eventos_ra = [e for e in eventos_ra if e["gratis"]]

    eventos_ra.sort(key=lambda x: x.get("asistentes", 0), reverse=True)

    return jsonify({
        "ciudad":     ciudad,
        "desde":      desde,
        "hasta":      hasta,
        "total_ra":   total_ra,
        "total":      len(eventos_ra),
        "eventos":    eventos_ra[:max_ev]
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
