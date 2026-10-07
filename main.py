from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import json
import os
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

SCRAPER_API_KEY = "00aa3ccf3ef48efc379726eacdccf27f"
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
RA_GRAPHQL_URL  = "https://ra.co/graphql"

AREAS_RA = {
    "london": 13, "berlin": 17, "amsterdam": 9, "paris": 15,
    "barcelona": 20, "madrid": 133, "ibiza": 29, "rome": 46,
    "milan": 152, "hamburg": 36, "lisbon": 72, "vienna": 32,
    "brussels": 22, "prague": 86, "budapest": 104, "stockholm": 84,
    "new york": 8, "los angeles": 2, "chicago": 3, "miami": 26,
    "buenos aires": 385, "sao paulo": 55, "mexico city": 411,
    "bogota": 473, "santiago": 387, "toronto": 71, "montreal": 75,
    "tokyo": 58, "melbourne": 63, "sydney": 64, "seoul": 94,
}

RA_QUERY = """
query GET_DEFAULT_EVENTS_LISTING($filters: FilterInputDtoInput, $pageSize: Int) {
  eventListings(filters: $filters, pageSize: $pageSize, page: 1,
    sort: { listingDate: { priority: 1, order: ASCENDING } }) {
    data {
      id
      listingDate
      event {
        id title date startTime endTime contentUrl cost attending
        venue { name address area { name id } }
        artists { name }
        pick { blurb }
      }
    }
    totalResults
  }
}
"""

SYSTEM_PROMPT = """Sos un asistente de planes para el fin de semana. Tu estilo es amigable, directo y canchero — como un amigo que conoce bien la movida nocturna. Hablás en español rioplatense (che, dale, copado, buenísimo, etc.) pero sin exagerar.

Tu trabajo es hacer preguntas para entender qué quiere el usuario y buscar eventos reales en Resident Advisor.

FLUJO — hacé UNA pregunta por vez, salteando las que ya respondió:
1. Ciudad (tenés disponibles: Buenos Aires, Berlin, Barcelona, London, Amsterdam, Madrid, New York, Paris, Ibiza, Rome, Lisbon, Tokyo, Melbourne, Mexico City, Bogota, Santiago, Sao Paulo, Toronto, Miami, Vienna, Prague, Budapest, Stockholm, Brussels, Hamburg)
2. Cuándo: viernes, sábado, o todo el finde
3. Horario: tarde (18hs+), noche (22hs+), madrugada (00hs+), o da igual
4. Lugar: cubierto (club/boliche/sala), aire libre (open air/parque/terraza), o da igual
5. Entrada: gratis, pago, o da igual

Cuando tengas ciudad + cuándo + horario + lugar, escribí exactamente este bloque al final de tu mensaje:
###FILTROS###
{"ciudad":"barcelona","cuando":"sabado","hora_min":"22:00","lugar":"cubierto","gratis":false,"max":10}
###FIN###

IMPORTANTE:
- Si el usuario dice algo como "el sábado a la noche en Berlin en un club", procesá todo de una y pedí solo lo que falta.
- Sé breve, máximo 2-3 líneas. Nada de listas largas.
- Cuando mostrés que vas a buscar, sé entusiasta pero breve.
- Si el usuario quiere buscar de nuevo, empezá desde el principio con buena onda."""

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

def buscar_ra(ciudad, desde, hasta, max_ev=10, hora_min=None, gratis=False):
    area = AREAS_RA.get(ciudad.lower().strip())
    if not area:
        return [], 0

    payload = {
        "operationName": "GET_DEFAULT_EVENTS_LISTING",
        "variables": {
            "filters": {
                "areas": {"eq": area},
                "listingDate": {"gte": f"{desde}T00:00:00.000Z", "lte": f"{hasta}T23:59:59.000Z"}
            },
            "pageSize": max_ev * 3
        },
        "query": RA_QUERY
    }

    try:
        data     = ra_request(payload)
        listings = data.get("data", {}).get("eventListings", {}).get("data", [])
        total    = data.get("data", {}).get("eventListings", {}).get("totalResults", 0)

        eventos = []
        for item in listings:
            ev = item.get("event")
            if not ev: continue
            venue = ev.get("venue") or {}
            cost  = (ev.get("cost") or "").strip()
            hora  = (ev.get("startTime") or "")[11:16]
            if hora_min and hora and hora >= "08:00" and hora < hora_min:
                continue
            es_gratis = cost == "" or cost.lower() in ["free", "gratis", "0"]
            if gratis and not es_gratis:
                continue
            eventos.append({
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

        eventos.sort(key=lambda x: x.get("asistentes", 0), reverse=True)
        return eventos[:max_ev], total
    except Exception as e:
        print(f"[RA ERROR] {e}")
        return [], 0

def call_groq(messages):
    print(f"[GROQ] Calling with key: {GROQ_API_KEY[:10]}... messages: {len(messages)}")
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "max_tokens": 800,
            "temperature": 0.7,
        },
        timeout=30
    )
    print(f"[GROQ] Status: {resp.status_code}")
    if not resp.ok:
        print(f"[GROQ ERROR] {resp.text}")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

def parse_filters(text):
    import re
    m = re.search(r'###FILTROS###\s*([\s\S]*?)\s*###FIN###', text)
    if not m: return None
    try: return json.loads(m.group(1))
    except: return None

def calc_fechas(cuando):
    hoy = datetime.now()
    dias = (4 - hoy.weekday()) % 7 or 7
    vier = hoy + timedelta(days=dias)
    sab  = vier + timedelta(days=1)
    dom  = vier + timedelta(days=2)
    fmt  = lambda d: d.strftime("%Y-%m-%d")
    if cuando == "viernes": return fmt(vier), fmt(vier)
    if cuando == "sabado":  return fmt(sab),  fmt(sab)
    return fmt(vier), fmt(dom)

@app.route("/")
def home():
    return jsonify({"status": "ok", "ciudades": list(AREAS_RA.keys())})

@app.route("/chat", methods=["POST"])
def chat():
    """Endpoint principal del chat — recibe historial y devuelve respuesta + eventos si aplica"""
    body     = request.json or {}
    messages = body.get("messages", [])

    if not messages:
        return jsonify({"error": "messages requerido"}), 400

    try:
        reply = call_groq(messages)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    filtros = parse_filters(reply)
    clean   = reply.replace(r'###FILTROS###[\s\S]*?###FIN###', '').strip()
    import re
    clean = re.sub(r'###FILTROS###[\s\S]*?###FIN###', '', reply).strip()

    eventos_out = []
    total_ra    = 0

    if filtros:
        desde, hasta = calc_fechas(filtros.get("cuando", "todo"))
        hora_min     = filtros.get("hora_min")
        gratis       = filtros.get("gratis", False)
        ciudad       = filtros.get("ciudad", "berlin")
        max_ev       = filtros.get("max", 10)

        eventos_out, total_ra = buscar_ra(ciudad, desde, hasta, max_ev, hora_min, gratis)

    return jsonify({
        "reply":    clean,
        "filtros":  filtros,
        "eventos":  eventos_out,
        "total_ra": total_ra,
    })

@app.route("/eventos")
def eventos():
    ciudad   = request.args.get("ciudad", "london").lower().strip()
    desde    = request.args.get("desde")
    hasta    = request.args.get("hasta")
    hora_min = request.args.get("hora_min")
    gratis   = request.args.get("gratis") == "true"
    max_ev   = int(request.args.get("max", 10))

    if not desde or not hasta:
        desde, hasta = finde_proximo()

    if ciudad not in AREAS_RA:
        return jsonify({"error": f"Ciudad '{ciudad}' no encontrada", "disponibles": list(AREAS_RA.keys())}), 400

    evs, total = buscar_ra(ciudad, desde, hasta, max_ev, hora_min, gratis)
    return jsonify({"ciudad": ciudad, "desde": desde, "hasta": hasta, "total_ra": total, "total": len(evs), "eventos": evs})

@app.route("/find-area")
def find_area():
    event_id = request.args.get("id", "2490511")
    payload = {
        "operationName": "GET_EVENT",
        "variables": {"id": event_id},
        "query": "query GET_EVENT($id: ID!) { event(id: $id) { id title venue { name address area { name id } } } }"
    }
    try:
        data  = ra_request(payload)
        event = data.get("data", {}).get("event")
        if not event: return jsonify({"error": "no encontrado"})
        venue = event.get("venue") or {}
        area  = venue.get("area") or {}
        return jsonify({"evento": event.get("title"), "venue": venue.get("name"), "ciudad": area.get("name"), "area_id": area.get("id")})
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
