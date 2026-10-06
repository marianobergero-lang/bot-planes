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
    # Europa
    "london":        13,
    "berlin":        17,
    "amsterdam":     9,
    "paris":         15,
    "barcelona":     20,  # confirmado!
    "madrid":        133,
    "ibiza":         29,
    "rome":          46,
    "milan":         152,
    "hamburg":       36,
    "cologne":       42,
    "brussels":      22,
    "lisbon":        72,
    "vienna":        32,
    "prague":        86,
    "warsaw":        98,
    "budapest":      104,
    "stockholm":     84,
    "zurich":        88,
    # Americas
    "new york":      8,
    "los angeles":   2,
    "chicago":       3,
    "buenos aires":  385,
    "sao paulo":     55,
    "mexico city":   411,
    "bogota":        473,
    "santiago":      387,
    "miami":         26,
    "toronto":       71,
    "montreal":      75,
    # Asia / Oceania
    "tokyo":         58,
    "melbourne":     63,
    "sydney":        64,
    "seoul":         94,
    "singapore":     102,
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
        venue { name address area { name id } }
        artists { name }
        pick { blurb }
        attending
      }
    }
    totalResults
  }
}
"""

EVENT_QUERY = """
query GET_EVENT($id: ID!) {
  event(id: $id) {
    id
    title
    date
    startTime
    contentUrl
    venue { name address area { name id } }
    artists { name }
    attending
  }
}
"""

def ra_request(payload):
    scraper_url = "https://api.scraperapi.com/"
    params  = {"api_key": SCRAPER_API_KEY, "url": RA_GRAPHQL_URL}
    headers = {"Content-Type": "application/json", "Origin": "https://ra.co", "Referer": "https://ra.co/events"}
    resp = requests.post(scraper_url, params=params, headers=headers, data=json.dumps(payload), timeout=30)
    return resp.json()

def finde_proximo():
    hoy = datetime.now()
    dias = (4 - hoy.weekday()) % 7 or 7
    viernes = hoy + timedelta(days=dias)
    domingo = viernes + timedelta(days=2)
    return viernes.strftime("%Y-%m-%d"), domingo.strftime("%Y-%m-%d")

@app.route("/")
def home():
    return jsonify({"status": "ok", "ciudades": list(AREAS_RA.keys())})

@app.route("/find-area")
def find_area():
    """Busca el área de un evento por su ID — así encontramos el código de Barcelona"""
    event_id = request.args.get("id", "2490511")
    payload = {
        "operationName": "GET_EVENT",
        "variables": {"id": event_id},
        "query": EVENT_QUERY
    }
    try:
        data  = ra_request(payload)
        event = data.get("data", {}).get("event")
        if not event:
            return jsonify({"error": "evento no encontrado", "raw": data})
        venue = event.get("venue") or {}
        area  = venue.get("area") or {}
        return jsonify({
            "evento":   event.get("title"),
            "venue":    venue.get("name"),
            "ciudad":   area.get("name"),
            "area_id":  area.get("id"),
            "direccion": venue.get("address"),
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/test-area")
def test_area():
    area  = int(request.args.get("area", 7))
    desde = request.args.get("desde", "2026-10-09")
    hasta = request.args.get("hasta", "2026-10-11")
    payload = {
        "operationName": "GET_DEFAULT_EVENTS_LISTING",
        "variables": {
            "filters": {
                "areas": {"eq": area},
                "listingDate": {"gte": f"{desde}T00:00:00.000Z", "lte": f"{hasta}T23:59:59.000Z"}
            },
            "pageSize": 3
        },
        "query": RA_QUERY
    }
    try:
        data     = ra_request(payload)
        listings = data.get("data", {}).get("eventListings", {}).get("data", [])
        total    = data.get("data", {}).get("eventListings", {}).get("totalResults", 0)
        return jsonify({"area": area, "total": total, "primer_evento": listings[0]["event"]["title"] if listings else None})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/eventos")
def eventos():
    ciudad   = request.args.get("ciudad", "london").lower().strip()
    desde    = request.args.get("desde")
    hasta    = request.args.get("hasta")
    hora_min = request.args.get("hora_min")
    gratis   = request.args.get("gratis")
    max_ev   = int(request.args.get("max", 10))

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
                "listingDate": {"gte": f"{desde}T00:00:00.000Z", "lte": f"{hasta}T23:59:59.000Z"}
            },
            "pageSize": max_ev * 2
        },
        "query": RA_QUERY
    }

    try:
        data     = ra_request(payload)
        listings = data.get("data", {}).get("eventListings", {}).get("data", [])
        total    = data.get("data", {}).get("eventListings", {}).get("totalResults", 0)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
            "artistas":   [a.get("name", "") for a in ev.get("artists", [])],
            "precio":     cost or "No especificado",
            "gratis":     es_gratis,
            "asistentes": ev.get("attending", 0),
            "destacado":  (ev.get("pick") or {}).get("blurb", ""),
            "url":        f"https://ra.co{ev.get('contentUrl', '')}",
            "fuente":     "Resident Advisor",
        })

    eventos_out.sort(key=lambda x: x.get("asistentes", 0), reverse=True)
    return jsonify({"ciudad": ciudad, "desde": desde, "hasta": hasta, "total_ra": total, "total": len(eventos_out), "eventos": eventos_out[:max_ev]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
