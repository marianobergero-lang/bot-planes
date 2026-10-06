from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from datetime import datetime, timedelta
import concurrent.futures

app = Flask(__name__)
CORS(app)

# ── ÁREAS DE RA ───────────────────────────────────────────────────────────────
AREAS_RA = {
    "buenos aires": 385, "berlin": 17, "barcelona": 7,
    "london": 13, "amsterdam": 9, "madrid": 133,
    "new york": 8, "paris": 15, "ibiza": 29,
    "mexico city": 411, "bogota": 473,
}

# ── CIUDADES EVENTBRITE ───────────────────────────────────────────────────────
CIUDADES_EB = {
    "buenos aires": "Buenos Aires, AR",
    "berlin": "Berlin, DE",
    "barcelona": "Barcelona, ES",
    "london": "London, GB",
    "amsterdam": "Amsterdam, NL",
    "madrid": "Madrid, ES",
    "new york": "New York, US",
    "paris": "Paris, FR",
    "ibiza": "Ibiza, ES",
    "mexico city": "Mexico City, MX",
    "bogota": "Bogota, CO",
}

# ── URLS MEETUP ───────────────────────────────────────────────────────────────
CIUDADES_MEETUP = {
    "buenos aires": "Buenos-Aires",
    "berlin": "Berlin",
    "barcelona": "Barcelona",
    "london": "London",
    "amsterdam": "Amsterdam",
    "madrid": "Madrid",
    "new york": "New-York",
    "paris": "Paris",
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

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─────────────────────────────────────────────────────────────────────────────
# FUENTE 1: RESIDENT ADVISOR
# ─────────────────────────────────────────────────────────────────────────────
def buscar_ra(ciudad, desde, hasta, max_ev=10):
    area = AREAS_RA.get(ciudad)
    if not area:
        return []

    headers = {
        **BROWSER_HEADERS,
        "Content-Type": "application/json",
        "Origin": "https://ra.co",
        "Referer": f"https://ra.co/events",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }

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
        session = requests.Session()
        session.get("https://ra.co", headers=BROWSER_HEADERS, timeout=10)
        resp = session.post("https://ra.co/graphql", json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        listings = data.get("data", {}).get("eventListings", {}).get("data", [])
        eventos = []
        for item in listings:
            ev = item.get("event")
            if not ev:
                continue
            venue = ev.get("venue") or {}
            cost = (ev.get("cost") or "").strip()
            eventos.append({
                "titulo":     ev.get("title", ""),
                "fecha":      ev.get("date", "")[:10],
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
        return eventos
    except Exception as e:
        print(f"[RA ERROR] {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# FUENTE 2: EVENTBRITE
# ─────────────────────────────────────────────────────────────────────────────
def buscar_eventbrite(ciudad, desde, hasta, max_ev=10):
    # Eventbrite tiene API pública que funciona sin key para búsquedas básicas
    ciudad_str = CIUDADES_EB.get(ciudad, ciudad)
    
    url = "https://www.eventbrite.com/api/v3/destination/search/"
    params = {
        "dedup": "true",
        "expand": "event_sales_status,image,saves,tickets,primary_venue,primary_organizer",
        "location.address": ciudad_str,
        "location.within": "25km",
        "start_date.range_start": f"{desde}T00:00:00",
        "start_date.range_end": f"{hasta}T23:59:59",
        "page_size": max_ev,
        "online_events_only": "false",
    }
    headers = {
        **BROWSER_HEADERS,
        "Referer": "https://www.eventbrite.com/",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        eventos = []
        for ev in data.get("events", {}).get("results", []):
            venue = ev.get("primary_venue") or {}
            addr = venue.get("address") or {}
            ticket_info = ev.get("ticket_availability") or {}
            gratis = ev.get("is_free", False)
            precio = "Gratis" if gratis else (ticket_info.get("minimum_ticket_price", {}) or {}).get("display", "Ver en Eventbrite")

            eventos.append({
                "titulo":    ev.get("name", {}).get("text", ""),
                "fecha":     (ev.get("start", {}).get("local", ""))[:10],
                "hora":      (ev.get("start", {}).get("local", ""))[11:16],
                "venue":     venue.get("name", ""),
                "direccion": addr.get("localized_address_display", ""),
                "artistas":  [],
                "precio":    precio,
                "gratis":    gratis,
                "asistentes": 0,
                "destacado": ev.get("summary", ""),
                "url":       ev.get("url", ""),
                "fuente":    "Eventbrite",
            })
        return eventos
    except Exception as e:
        print(f"[EVENTBRITE ERROR] {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# FUENTE 3: MEETUP (scraping simple)
# ─────────────────────────────────────────────────────────────────────────────
def buscar_meetup(ciudad, desde, hasta, max_ev=10):
    ciudad_slug = CIUDADES_MEETUP.get(ciudad)
    if not ciudad_slug:
        return []

    url = f"https://www.meetup.com/find/?location={ciudad_slug}&source=EVENTS&eventType=inPerson&startDateRange={desde}&endDateRange={hasta}"
    
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        # Meetup requiere JS para cargar eventos, así que intentamos la API
        api_url = f"https://api.meetup.com/find/events"
        params = {
            "location": ciudad_slug,
            "start_date_range": desde,
            "end_date_range": hasta,
            "page": max_ev,
            "fields": "featured_photo,self",
        }
        resp2 = requests.get(api_url, params=params, headers=BROWSER_HEADERS, timeout=15)
        if resp2.status_code == 200:
            eventos = []
            for ev in resp2.json():
                venue = ev.get("venue") or {}
                eventos.append({
                    "titulo":    ev.get("name", ""),
                    "fecha":     ev.get("local_date", ""),
                    "hora":      ev.get("local_time", ""),
                    "venue":     venue.get("name", ""),
                    "direccion": venue.get("address_1", ""),
                    "artistas":  [],
                    "precio":    "Ver en Meetup",
                    "gratis":    ev.get("fee") is None,
                    "asistentes": ev.get("yes_rsvp_count", 0),
                    "destacado": ev.get("description", "")[:200] if ev.get("description") else "",
                    "url":       ev.get("link", ""),
                    "fuente":    "Meetup",
                })
            return eventos
        return []
    except Exception as e:
        print(f"[MEETUP ERROR] {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# FUENTE 4: XCEED (scraping)
# ─────────────────────────────────────────────────────────────────────────────
def buscar_xceed(ciudad, desde, hasta, max_ev=10):
    # Xceed tiene una API interna que podemos usar
    ciudad_map = {
        "berlin": "berlin", "barcelona": "barcelona", "madrid": "madrid",
        "london": "london", "amsterdam": "amsterdam", "paris": "paris",
        "ibiza": "ibiza", "buenos aires": "buenos-aires",
    }
    ciudad_slug = ciudad_map.get(ciudad)
    if not ciudad_slug:
        return []

    url = f"https://xceed.me/en/{ciudad_slug}/party"
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        # Xceed carga con JS, intentamos su API interna
        api_url = f"https://xceed.me/api/v2/events"
        params = {
            "city": ciudad_slug,
            "from": desde,
            "to": hasta,
            "limit": max_ev,
        }
        resp2 = requests.get(api_url, params=params, headers={
            **BROWSER_HEADERS,
            "Referer": "https://xceed.me/",
        }, timeout=15)
        
        if resp2.status_code == 200:
            data = resp2.json()
            eventos = []
            for ev in (data.get("data") or data.get("events") or []):
                eventos.append({
                    "titulo":    ev.get("name", ev.get("title", "")),
                    "fecha":     (ev.get("start_date") or ev.get("date", ""))[:10],
                    "hora":      (ev.get("start_date") or ev.get("date", ""))[11:16],
                    "venue":     (ev.get("venue") or {}).get("name", ""),
                    "direccion": (ev.get("venue") or {}).get("address", ""),
                    "artistas":  [a.get("name","") for a in ev.get("artists", [])],
                    "precio":    ev.get("price", "Ver en Xceed"),
                    "gratis":    ev.get("price") == "0" or ev.get("is_free", False),
                    "asistentes": ev.get("going_count", 0),
                    "destacado": ev.get("description", "")[:200] if ev.get("description") else "",
                    "url":       f"https://xceed.me{ev.get('url', '')}",
                    "fuente":    "Xceed",
                })
            return eventos
        return []
    except Exception as e:
        print(f"[XCEED ERROR] {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# FUENTE 5: FEVER (scraping)
# ─────────────────────────────────────────────────────────────────────────────
def buscar_fever(ciudad, desde, hasta, max_ev=10):
    ciudad_map = {
        "buenos aires": "buenos-aires", "berlin": "berlin",
        "barcelona": "barcelona", "madrid": "madrid",
        "london": "london", "amsterdam": "amsterdam",
        "new york": "new-york", "paris": "paris",
    }
    ciudad_slug = ciudad_map.get(ciudad)
    if not ciudad_slug:
        return []

    try:
        # Fever tiene una API interna
        url = "https://api.feverup.com/api/discovery"
        params = {
            "city_id": ciudad_slug,
            "lang": "en",
            "limit": max_ev,
            "date_from": desde,
            "date_to": hasta,
        }
        resp = requests.get(url, params=params, headers={
            **BROWSER_HEADERS,
            "Referer": "https://feverup.com/",
            "Origin": "https://feverup.com",
        }, timeout=15)

        if resp.status_code == 200:
            data = resp.json()
            eventos = []
            for ev in (data.get("plans") or data.get("data") or []):
                eventos.append({
                    "titulo":    ev.get("title", ev.get("name", "")),
                    "fecha":     (ev.get("date") or desde)[:10],
                    "hora":      (ev.get("time") or "")[:5],
                    "venue":     ev.get("location_name", ""),
                    "direccion": ev.get("address", ""),
                    "artistas":  [],
                    "precio":    ev.get("price_from", "Ver en Fever"),
                    "gratis":    ev.get("price_from") == "0" or ev.get("is_free", False),
                    "asistentes": ev.get("saves", 0),
                    "destacado": ev.get("description", "")[:200] if ev.get("description") else "",
                    "url":       f"https://feverup.com{ev.get('slug', '')}",
                    "fuente":    "Fever",
                })
            return eventos
        return []
    except Exception as e:
        print(f"[FEVER ERROR] {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "mensaje": "Bot de Planes API — Fuentes: RA, Eventbrite, Meetup, Xceed, Fever",
        "fuentes": ["resident_advisor", "eventbrite", "meetup", "xceed", "fever"]
    })

@app.route("/eventos")
def eventos():
    ciudad   = request.args.get("ciudad", "berlin").lower().strip()
    desde    = request.args.get("desde")
    hasta    = request.args.get("hasta")
    hora_min = request.args.get("hora_min")
    gratis   = request.args.get("gratis")
    max_ev   = int(request.args.get("max", 10))
    fuentes  = request.args.get("fuentes", "ra,eventbrite,meetup,xceed,fever").split(",")

    # Fechas por defecto: próximo finde
    if not desde or not hasta:
        hoy = datetime.now()
        dias = (4 - hoy.weekday()) % 7 or 7
        viernes = hoy + timedelta(days=dias)
        domingo = viernes + timedelta(days=2)
        desde = viernes.strftime("%Y-%m-%d")
        hasta = domingo.strftime("%Y-%m-%d")

    # Buscar en paralelo en todas las fuentes
    todos = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        if "ra" in fuentes:
            futures["ra"] = executor.submit(buscar_ra, ciudad, desde, hasta, max_ev)
        if "eventbrite" in fuentes:
            futures["eventbrite"] = executor.submit(buscar_eventbrite, ciudad, desde, hasta, max_ev)
        if "meetup" in fuentes:
            futures["meetup"] = executor.submit(buscar_meetup, ciudad, desde, hasta, max_ev)
        if "xceed" in fuentes:
            futures["xceed"] = executor.submit(buscar_xceed, ciudad, desde, hasta, max_ev)
        if "fever" in fuentes:
            futures["fever"] = executor.submit(buscar_fever, ciudad, desde, hasta, max_ev)

        resultados = {}
        for nombre, future in futures.items():
            try:
                resultados[nombre] = future.result(timeout=20)
                todos.extend(resultados[nombre])
            except Exception as e:
                print(f"[{nombre.upper()} TIMEOUT/ERROR] {e}")
                resultados[nombre] = []

    # Filtros
    if hora_min:
        todos = [e for e in todos if not e["hora"] or e["hora"] >= hora_min or e["hora"] < "08:00"]
    if gratis == "true":
        todos = [e for e in todos if e["gratis"]]

    # Eliminar duplicados por título similar
    vistos = set()
    sin_duplicados = []
    for ev in todos:
        key = ev["titulo"].lower()[:30]
        if key not in vistos:
            vistos.add(key)
            sin_duplicados.append(ev)

    # Ordenar por asistentes (más popular primero)
    sin_duplicados.sort(key=lambda x: x.get("asistentes", 0), reverse=True)

    return jsonify({
        "ciudad":  ciudad,
        "desde":   desde,
        "hasta":   hasta,
        "total":   len(sin_duplicados),
        "por_fuente": {k: len(v) for k, v in resultados.items()},
        "eventos": sin_duplicados[:max_ev]
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
