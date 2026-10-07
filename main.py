from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import json
import os
import re
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

# Query por fecha/ciudad
RA_QUERY_FECHA = """
query GET_DEFAULT_EVENTS_LISTING($filters: FilterInputDtoInput, $pageSize: Int, $page: Int) {
  eventListings(filters: $filters, pageSize: $pageSize, page: $page,
    sort: { listingDate: { priority: 1, order: ASCENDING } }) {
    data {
      id listingDate
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

# Query por artista
RA_QUERY_ARTISTA = """
query GET_ARTIST_EVENTS($slug: String!, $pageSize: Int) {
  artist(slug: $slug) {
    name
    eventListings(pageSize: $pageSize) {
      data {
        event {
          id title date startTime contentUrl cost attending
          venue { name address area { name } }
          artists { name }
        }
      }
    }
  }
}
"""

# Query busqueda general
RA_QUERY_SEARCH = """
query SEARCH_EVENTS($query: String!, $pageSize: Int) {
  eventListings(
    filters: { title: { contains: $query } }
    pageSize: $pageSize
    page: 1
    sort: { listingDate: { priority: 1, order: ASCENDING } }
  ) {
    data {
      event {
        id title date startTime contentUrl cost attending
        venue { name address area { name } }
        artists { name }
        pick { blurb }
      }
    }
    totalResults
  }
}
"""

SYSTEM_PROMPT = """Sos un asistente experto en planes para salir, especialmente electrónica y cultura de club. Tu estilo es amigable, directo y canchero — como un amigo que conoce bien la movida. Hablás en español rioplatense pero sin exagerar.

Podés buscar eventos de tres formas distintas:
1. Por fecha + ciudad: "qué hay el sábado en Berlin"
2. Por artista/DJ: "dónde toca Nina Kraviz" o "hay algo de Amelie Lens"
3. Por venue/evento: "qué hay en Berghain" o "busco Brunch Elektrónik"

FLUJO — detectá qué tipo de búsqueda quiere el usuario y hacé UNA pregunta por vez:

Si busca por FECHA+CIUDAD necesitás:
- Ciudad
- Cuándo — interpretá así:
  * "este finde" / "el finde" / "fin de semana" → "todo" (viernes + sábado + domingo)
  * "el viernes" → "viernes"
  * "el sábado" → "sabado"
  * "próxima semana" o vago → confirmá con el usuario: "¿Te referís al finde del viernes X al domingo X?"
- Horario (tarde 18hs+, noche 22hs+, madrugada 00hs+, da igual)
- Lugar (cubierto/club, aire libre, da igual)
- Precio (gratis, barato <15€, normal 15-30€, caro >30€, da igual)

Si busca por ARTISTA necesitás:
- Nombre del artista
- Ciudad (opcional)

Si busca por VENUE/DISCO necesitás:
- Nombre del venue/disco o evento
- Ciudad (opcional)
- Cuándo — SIEMPRE preguntá la fecha antes de buscar: "¿Para cuándo? ¿Este finde, el viernes, el sábado...?"

Cuando tengas suficiente info, escribí este bloque al final:
###FILTROS###
{
  "tipo": "fecha",
  "ciudad": "barcelona",
  "cuando": "sabado",
  "hora_min": "22:00",
  "lugar": "cubierto",
  "precio_max": 30,
  "gratis": false,
  "max": 8
}
###FIN###

O para artista:
###FILTROS###
{
  "tipo": "artista",
  "artista": "nina kraviz",
  "ciudad": "barcelona",
  "cuando": "todo",
  "max": 5
}
###FIN###

O para venue/disco (SIEMPRE pedí fecha antes de buscar):
###FILTROS###
{
  "tipo": "venue",
  "venue": "Les Enfants Brillants",
  "ciudad": "barcelona",
  "cuando": "todo",
  "max": 8
}
###FIN###

O para evento específico:
###FILTROS###
{
  "tipo": "busqueda",
  "query": "Brunch Elektrónik",
  "ciudad": "barcelona",
  "max": 5
}
###FIN###

IMPORTANTE:
- Si el usuario dice "el sábado a la noche en Berlin en un club", procesá todo de una.
- Para artistas, convertí el nombre a slug: "Nina Kraviz" → "nina-kraviz", "Amelie Lens" → "amelie-lens"
- Sé breve, máximo 2-3 líneas. Conversacional y con onda.
- Géneros que conocés: techno, house, progressive, minimal, drum&bass, reggaeton, cumbia, jazz, indie, pop, rock, electrónica en general.
- Si el usuario menciona un género, guardalo para sugerirle eventos afines.
- Cuando saludés al usuario por primera vez, decí exactamente: "¡Hola! ¿Qué plan estás buscando? Podés decirme una ciudad, un DJ/artista, un venue/disco o una fecha determinada... y buscamos tu plan ideal!"
- Siempre escribí "venue/disco" cuando te refieras a un lugar."""

def corregir_ciudad(ciudad_input):
    """Corrige errores tipográficos en nombres de ciudades"""
    from difflib import get_close_matches
    ciudad = ciudad_input.lower().strip()
    if ciudad in AREAS_RA:
        return ciudad, True
    matches = get_close_matches(ciudad, AREAS_RA.keys(), n=1, cutoff=0.6)
    if matches:
        return matches[0], True
    return ciudad, False

def corregir_artista(artista_input, lista_artistas):
    """Corrige errores tipográficos en nombres de artistas"""
    from difflib import get_close_matches
    artista = artista_input.lower().strip()
    lista_lower = {a.lower(): a for a in lista_artistas}
    if artista in lista_lower:
        return lista_lower[artista], True
    matches = get_close_matches(artista, lista_lower.keys(), n=1, cutoff=0.6)
    if matches:
        return lista_lower[matches[0]], True
    return artista_input, False

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

def normalizar_cuando(cuando):
    """Normaliza distintas formas de decir cuándo"""
    if not cuando:
        return "todo"
    c = cuando.lower().strip()
    if any(x in c for x in ["viernes", "friday", "vie"]):
        return "viernes"
    if any(x in c for x in ["sabado", "sábado", "saturday", "sab"]):
        return "sabado"
    return "todo"  # finde, todo, weekend, este finde, etc.

def calc_fechas(cuando):
    cuando = normalizar_cuando(cuando)
    hoy = datetime.now()
    dias = (4 - hoy.weekday()) % 7 or 7
    vier = hoy + timedelta(days=dias)
    sab  = vier + timedelta(days=1)
    dom  = vier + timedelta(days=2)
    fmt  = lambda d: d.strftime("%Y-%m-%d")
    if cuando == "viernes": return fmt(vier), fmt(vier)
    if cuando == "sabado":  return fmt(sab),  fmt(sab)
    return fmt(vier), fmt(dom)  # todo = viernes + sábado + domingo

def precio_categoria(cost_str):
    """Convierte string de precio a número aproximado"""
    if not cost_str or cost_str.strip() == "":
        return None
    cost = cost_str.lower().strip()
    if cost in ["free", "gratis", "0"]:
        return 0
    nums = re.findall(r'\d+', cost)
    if nums:
        return int(nums[0])
    return None

def formatear_evento(ev, venue_data=None):
    venue = ev.get("venue") or {}
    cost  = (ev.get("cost") or "").strip()
    hora  = (ev.get("startTime") or "")[11:16]
    artistas = [a.get("name", "") for a in ev.get("artists", [])]
    precio_num = precio_categoria(cost)

    if precio_num == 0:
        precio_label = "Gratis"
        es_gratis = True
    elif precio_num is not None and precio_num < 15:
        precio_label = f"Barato · {cost}"
        es_gratis = False
    elif precio_num is not None and precio_num <= 30:
        precio_label = f"Normal · {cost}"
        es_gratis = False
    elif precio_num is not None:
        precio_label = f"Caro · {cost}"
        es_gratis = False
    else:
        precio_label = "Ver en RA"
        es_gratis = False

    return {
        "titulo":       ev.get("title", ""),
        "fecha":        (ev.get("date") or "")[:10],
        "hora":         hora,
        "venue":        venue.get("name", ""),
        "direccion":    venue.get("address", ""),
        "ciudad_venue": (venue.get("area") or {}).get("name", ""),
        "artistas":     artistas,
        "precio":       cost or "No especificado",
        "precio_label": precio_label,
        "precio_num":   precio_num,
        "gratis":       es_gratis,
        "asistentes":   ev.get("attending", 0),
        "destacado":    (ev.get("pick") or {}).get("blurb", ""),
        "url":          f"https://ra.co{ev.get('contentUrl', '')}",
        "fuente":       "Resident Advisor",
    }

def buscar_por_fecha(ciudad, desde, hasta, max_ev=8, hora_min=None, gratis=False, precio_max=None):
    area = AREAS_RA.get(ciudad.lower().strip())
    if not area:
        return [], 0

    # Paginamos para traer TODOS los eventos de RA
    listings = []
    total    = 0
    page     = 1
    page_size = 100

    try:
        while True:
            payload = {
                "operationName": "GET_DEFAULT_EVENTS_LISTING",
                "variables": {
                    "filters": {
                        "areas": {"eq": area},
                        "listingDate": {"gte": f"{desde}T00:00:00.000Z", "lte": f"{hasta}T23:59:59.000Z"}
                    },
                    "pageSize": page_size,
                    "page": page
                },
                "query": RA_QUERY_FECHA
            }
            # Necesitamos page en la query
            payload["variables"]["page"] = page
            data  = ra_request(payload)
            page_listings = data.get("data", {}).get("eventListings", {}).get("data", [])
            total = data.get("data", {}).get("eventListings", {}).get("totalResults", 0)
            listings.extend(page_listings)

            # Si ya tenemos todos o no hay más, paramos
            if len(listings) >= total or len(page_listings) < page_size:
                break
            page += 1
            # Máximo 3 páginas (300 eventos) para no sobrecargar
            if page > 3:
                break

    except Exception as e:
        print(f"[RA FECHA ERROR] {e}")
        return [], 0

    try:

        eventos = []
        for item in listings:
            ev = item.get("event")
            if not ev: continue

            ev_fmt = formatear_evento(ev)
            hora = ev_fmt["hora"]

            # Filtro hora
            if hora_min and hora and hora >= "08:00" and hora < hora_min:
                continue
            # Filtro gratis
            if gratis and not ev_fmt["gratis"]:
                continue
            # Filtro precio máximo
            if precio_max is not None and ev_fmt["precio_num"] is not None and ev_fmt["precio_num"] > precio_max:
                continue

            eventos.append(ev_fmt)

        # Ordenar por fecha primero, luego popularidad dentro de cada día
        eventos.sort(key=lambda x: (x.get("fecha", ""), -x.get("asistentes", 0)))
        return eventos[:max_ev], total

    except Exception as e:
        print(f"[RA FECHA ERROR 2] {e}")
        return [], 0

def buscar_por_busqueda(query, ciudad=None, max_ev=5):
    """Busca por nombre de evento o venue"""
    payload = {
        "operationName": "SEARCH_EVENTS",
        "variables": {"query": query, "pageSize": max_ev * 2},
        "query": RA_QUERY_SEARCH
    }
    try:
        data     = ra_request(payload)
        listings = data.get("data", {}).get("eventListings", {}).get("data", [])
        total    = data.get("data", {}).get("eventListings", {}).get("totalResults", 0)
        eventos  = [formatear_evento(item["event"]) for item in listings if item.get("event")]
        if ciudad:
            eventos = [e for e in eventos if ciudad.lower() in (e.get("ciudad_venue") or "").lower()]
        eventos.sort(key=lambda x: x.get("asistentes", 0), reverse=True)
        return eventos[:max_ev], total
    except Exception as e:
        print(f"[RA SEARCH ERROR] {e}")
        return [], 0

def call_groq(messages):
    print(f"[GROQ] key: {GROQ_API_KEY[:10]}... msgs: {len(messages)}")
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "openai/gpt-oss-120b",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "max_tokens": 800,
            "temperature": 0.7,
        },
        timeout=30
    )
    print(f"[GROQ] status: {resp.status_code}")
    if not resp.ok:
        print(f"[GROQ ERROR] {resp.text}")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

def parse_filters(text):
    m = re.search(r'###FILTROS###\s*([\s\S]*?)\s*###FIN###', text)
    if not m: return None
    try: return json.loads(m.group(1))
    except: return None

@app.route("/")
def home():
    return jsonify({"status": "ok", "version": "fase1", "ciudades": list(AREAS_RA.keys())})

@app.route("/chat", methods=["POST"])
def chat():
    body     = request.json or {}
    messages = body.get("messages", [])
    if not messages:
        return jsonify({"error": "messages requerido"}), 400

    try:
        reply = call_groq(messages)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    filtros = parse_filters(reply)
    clean   = re.sub(r'###FILTROS###[\s\S]*?###FIN###', '', reply).strip()

    eventos_out = []
    total_ra    = 0

    if filtros:
        tipo = filtros.get("tipo", "fecha")
        ciudad_raw = filtros.get("ciudad", "berlin")

        # Corrección fuzzy de ciudad
        ciudad_corregida, ciudad_ok = corregir_ciudad(ciudad_raw)
        if not ciudad_ok:
            return jsonify({
                "reply": f"No encontré la ciudad '{ciudad_raw}'. ¿Podés indicarme el nombre exacto? Las ciudades disponibles son: {', '.join(list(AREAS_RA.keys())[:10])}...",
                "filtros": None, "eventos": [], "total_ra": 0
            })
        if ciudad_corregida != ciudad_raw.lower():
            clean = f"(Entendí '{ciudad_corregida.title()}' por '{ciudad_raw}') " + clean

        if tipo == "fecha":
            desde, hasta = calc_fechas(filtros.get("cuando", "todo"))
            eventos_out, total_ra = buscar_por_fecha(
                ciudad     = ciudad_corregida,
                desde      = desde,
                hasta      = hasta,
                max_ev     = filtros.get("max", 8),
                hora_min   = filtros.get("hora_min"),
                gratis     = filtros.get("gratis", False),
                precio_max = filtros.get("precio_max"),
            )

        elif tipo == "busqueda":
            eventos_out, total_ra = buscar_por_busqueda(
                query  = filtros.get("query", ""),
                ciudad = ciudad_corregida,
                max_ev = filtros.get("max", 5),
            )

        elif tipo == "artista":
            artista_raw = filtros.get("artista", "").replace("-", " ")
            desde, hasta = calc_fechas(filtros.get("cuando", "todo"))
            todos, _ = buscar_por_fecha(ciudad_corregida, desde, hasta, max_ev=100)

            todos_artistas = list({a for ev in todos for a in ev.get("artistas", [])})
            artista_corregido, artista_ok = corregir_artista(artista_raw, todos_artistas)

            if artista_ok and artista_corregido.lower() != artista_raw.lower():
                clean = f"(Entendí '{artista_corregido}' por '{artista_raw}') " + clean

            eventos_out = [e for e in todos if any(
                artista_corregido.lower() in a.lower() for a in e.get("artistas", [])
            )]

            if not eventos_out and not artista_ok:
                return jsonify({
                    "reply": f"No encontré a '{artista_raw}' en eventos de {ciudad_corregida} este finde. ¿Podés verificar el nombre exacto del artista?",
                    "filtros": filtros, "eventos": [], "total_ra": 0
                })

            eventos_out = eventos_out[:filtros.get("max", 5)]
            total_ra = len(eventos_out)

        elif tipo == "venue":
            venue_raw = filtros.get("venue", "")
            desde, hasta = calc_fechas(filtros.get("cuando", "todo"))
            todos, _ = buscar_por_fecha(ciudad_corregida, desde, hasta, max_ev=100)

            # Fuzzy matching de venue
            todos_venues = list({e.get("venue", "") for e in todos if e.get("venue")})
            from difflib import get_close_matches
            matches = get_close_matches(venue_raw.lower(), [v.lower() for v in todos_venues], n=1, cutoff=0.5)

            if matches:
                venue_match = matches[0]
                if venue_match != venue_raw.lower():
                    clean = f"(Entendí '{venue_match.title()}' por '{venue_raw}') " + clean
                eventos_out = [e for e in todos if venue_match in e.get("venue", "").lower()]
            else:
                # Búsqueda parcial
                eventos_out = [e for e in todos if venue_raw.lower() in e.get("venue", "").lower()]

            if not eventos_out:
                return jsonify({
                    "reply": f"No encontré eventos en '{venue_raw}' en {ciudad_corregida} este finde. ¿Podés verificar el nombre exacto del venue?",
                    "filtros": filtros, "eventos": [], "total_ra": 0
                })

            eventos_out = eventos_out[:filtros.get("max", 8)]
            total_ra = len(eventos_out)

    return jsonify({
        "reply":    clean,
        "filtros":  filtros,
        "eventos":  eventos_out,
        "total_ra": total_ra,
    })

@app.route("/eventos")
def eventos():
    ciudad    = request.args.get("ciudad", "london").lower().strip()
    desde     = request.args.get("desde")
    hasta     = request.args.get("hasta")
    hora_min  = request.args.get("hora_min")
    gratis    = request.args.get("gratis") == "true"
    precio_max = int(request.args.get("precio_max", 9999))
    max_ev    = int(request.args.get("max", 8))

    if not desde or not hasta:
        desde, hasta = finde_proximo()

    if ciudad not in AREAS_RA:
        return jsonify({"error": f"Ciudad '{ciudad}' no encontrada"}), 400

    evs, total = buscar_por_fecha(ciudad, desde, hasta, max_ev, hora_min, gratis, precio_max)
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
