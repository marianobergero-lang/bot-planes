from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import json
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

SCRAPER_API_KEY = "00aa3ccf3ef48efc379726eacdccf27f"
RA_GRAPHQL_URL  = "https://ra.co/graphql"

AREAS_RA = {
    "buenos aires": 385, "berlin": 17, "barcelona": 7,
    "london": 13, "amsterdam": 9, "madrid": 133,
    "new york": 8, "paris": 15, "ibiza": 29,
    "mexico city": 411, "bogota": 473,
}

RA_QUERY = """
query GET_DEFAULT_EVENTS_LISTING($filters: FilterInputDtoInput, $pageSize: Int) {
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

def finde_proximo():
    hoy = datetime.now()
    dias = (4 - hoy.weekday()) % 7 or 7
    viernes = hoy + timedelta(days=dias)
    domingo = viernes + timedelta(days=2)
    return viernes.strftime("%Y-%m-%d"), domingo.strftime("%Y-%m-%d")

@app.route("/")
def home():
    return jsonify({"status": "ok", "mensaje": "Bot de Planes API v3 con ScraperAPI"})

@app.route("/test")
def test():
    """Endpoint de debug — muestra exactamente qué devuelve RA"""
    ciudad = request.args.get("ciudad", "barcelona").lower().strip()
    desde  = request.args.get("desde", "2026-10-01")
    hasta  = request.args.get("hasta", "2026-10-31")
    area   = AREAS_RA.get(ciudad, 7)

    payload = {
        "operationName": "GET_DEFAULT_EVENTS_LISTING",
        "variables": {
            "filters": {
                "areas": {"eq": area},
                "listingDate": {
                    "gte": f"{desde}T00:00:00.000Z",
                    "lte": f"{hasta}T23:59:59.000Z",
                }
            },
            "pageSize": 5
        },
        "query": RA_QUERY
    }

    # Intento 1: directo sin ScraperAPI
    try:
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://ra.co",
            "Referer": "https://ra.co/events",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        resp = requests.post(RA_GRAPHQL_URL, json=payload, headers=headers, timeout=15)
        directo = {"status": resp.status_code, "data": resp.json()}
    except Exception as e:
        directo = {"error": str(e)}

    # Intento 2: via ScraperAPI
    try:
        scraper_url = "https://api.scraperapi.com/"
        params = {"api_key": SCRAPER_API_KEY, "url": RA_GRAPHQL_URL}
        headers2 = {
            "Content-Type": "application/json",
            "Origin": "https://ra.co",
            "Referer": "https://ra.co/events",
        }
        resp2 = requests.post(
            scraper_url,
            params=params,
            headers=headers2,
            data=json.dumps(payload),
            timeout=30
        )
        via_scraper = {"status": resp2.status_code, "data": resp2.json()}
    except Exception as e:
        via_scraper = {"error": str(e)}

    return jsonify({
        "ciudad": ciudad,
        "area": area,
        "desde": desde,
        "hasta": hasta,
        "directo": directo,
        "via_scraper": via_scraper,
    })

@app.route("/eventos")
def eventos():
    ciudad  = request.args.get("ciudad", "berlin").lower().strip()
    desde   = request.args.get("desde")
    hasta   = request.args.get("hasta")
    hora_min = request.args.get("hora_min")
    gratis  = request.args.get("gratis")
    max_ev  = int(request.args.get("max", 10))

    if not desde or not hasta:
        desde, hasta = finde_proximo()

    area = AREAS_RA.get(ciudad)
    if not area:
        return jsonify({"error": f"Ciudad '{ciudad}' no encontrada", "disponibles": list(AREAS_RA.keys())}), 400

    payload = {
        "operationName": "GET_DEFAULT_EVENTS_LISTING",
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
        scraper_url = "https://api.scraperapi.com/"
        params = {"api_key": SCRAPER_API_KEY, "url": RA_GRAPHQL_URL}
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://ra.co",
            "Referer": "https://ra.co/events",
        }
        resp = requests.post(scraper_url, params=params, headers=headers, data=json.dumps(payload), timeout=30)
        data = resp.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    listings = data.get("data", {}).get("eventListings", {}).get("data", [])
    total    = data.get("data", {}).get("eventListings", {}).get("totalResults", 0)

    eventos_out = []
    for item in listings:
        ev = item.get("event")
        if not ev:
            continue
        venue = ev.get("venue") or {}
        cost  = (ev.get("cost") or "").strip()
        hora  = (ev.get("startTime") or "")[:5]

        if hora_min and hora and hora >= "08:00" and hora < hora_min:
            continue
        es_gratis = cost == "" or cost.lower() in ["free", "gratis", "0"]
        if gratis == "true" and not es_gratis:
            continue

        eventos_out.append({
            "titulo":     ev.get("title", ""),
            "fecha":      (ev.get("date") or "")[:10],
            "hora":       hora,
            "venue":      venue.get("name", ""),
            "direccion":  venue.get("address", ""),
            "artistas":   [a["displayName"] for a in ev.get("artists", [])],
            "precio":     cost or "No especificado",
            "gratis":     es_gratis,
            "asistentes": ev.get("attending", 0),
            "destacado":  (ev.get("pick") or {}).get("blurb", ""),
            "url":        f"https://ra.co{ev.get('contentUrl', '')}",
            "fuente":     "Resident Advisor",
        })

    eventos_out.sort(key=lambda x: x.get("asistentes", 0), reverse=True)

    return jsonify({
        "ciudad":   ciudad,
        "desde":    desde,
        "hasta":    hasta,
        "total_ra": total,
        "total":    len(eventos_out),
        "eventos":  eventos_out[:max_ev]
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
