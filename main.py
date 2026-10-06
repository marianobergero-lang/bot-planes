from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

RA_URL = "https://ra.co/graphql"
HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://ra.co/events",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": "https://ra.co",
}

AREAS = {
    "buenos aires": 385, "berlin": 17, "barcelona": 7,
    "london": 13, "amsterdam": 9, "madrid": 133,
    "new york": 8, "paris": 15, "ibiza": 29,
    "mexico city": 411, "bogota": 473,
}

QUERY = """
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
        venue {
          name
          address
          capacity
          area { name }
        }
        artists { displayName }
        pick { blurb }
        attending
      }
    }
    totalResults
  }
}
"""

@app.route("/")
def home():
    return jsonify({"status": "ok", "message": "Bot de Planes API funcionando!"})

@app.route("/eventos")
def eventos():
    ciudad   = request.args.get("ciudad", "berlin").lower()
    desde    = request.args.get("desde")
    hasta    = request.args.get("hasta")
    hora_min = request.args.get("hora_min")
    gratis   = request.args.get("gratis")   # "true" o "false"
    max_ev   = int(request.args.get("max", 10))

    # Fechas por defecto: próximo finde
    if not desde or not hasta:
        hoy = datetime.now()
        dias = (4 - hoy.weekday()) % 7 or 7
        viernes = hoy + timedelta(days=dias)
        domingo = viernes + timedelta(days=2)
        desde = viernes.strftime("%Y-%m-%d")
        hasta = domingo.strftime("%Y-%m-%d")

    area = AREAS.get(ciudad)
    if not area:
        return jsonify({"error": f"Ciudad '{ciudad}' no encontrada", "disponibles": list(AREAS.keys())}), 400

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
            "pageSize": max_ev * 3
        },
        "query": QUERY
    }

    try:
        resp = requests.post(RA_URL, json=payload, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    listings = data.get("data", {}).get("eventListings", {}).get("data", [])
    total    = data.get("data", {}).get("eventListings", {}).get("totalResults", 0)

    eventos = []
    for item in listings:
        ev = item.get("event")
        if not ev:
            continue

        venue   = ev.get("venue") or {}
        cost    = ev.get("cost", "") or ""
        hora    = (ev.get("startTime") or "")[:5]
        artistas = [a["displayName"] for a in ev.get("artists", [])]

        # Filtro hora mínima
        if hora_min and hora and hora < hora_min and hora >= "08:00":
            continue

        # Filtro gratis
        es_gratis = cost.strip() == "" or cost.strip().lower() in ["free", "gratis", "0", "£0", "$0"]
        if gratis == "true" and not es_gratis:
            continue

        eventos.append({
            "titulo":    ev.get("title", ""),
            "fecha":     ev.get("date", ""),
            "hora":      hora,
            "venue":     venue.get("name", ""),
            "direccion": venue.get("address", ""),
            "artistas":  artistas,
            "precio":    cost if cost else "No especificado",
            "gratis":    es_gratis,
            "asistentes": ev.get("attending", 0),
            "destacado": (ev.get("pick") or {}).get("blurb", ""),
            "url":       f"https://ra.co{ev.get('contentUrl', '')}",
        })

    return jsonify({
        "ciudad":  ciudad,
        "desde":   desde,
        "hasta":   hasta,
        "total":   total,
        "eventos": eventos[:max_ev]
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
