from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import json
import os
import re
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
RA_GRAPHQL_URL  = "https://ra.co/graphql"

AREAS_RA = {
    # Europa — verificados ✅
    "london":        13,
    "berlin":        34,
    "amsterdam":     29,
    "barcelona":     20,
    "madrid":        41,
    "ibiza":         25,
    "mallorca":      661,
    "valencia":      607,
    "paris":         44,
    "rome":          351,
    "milan":         347,
    "munich":        151,
    "lisbon":        53,
    "porto":         364,
    "turin":         348,
    "south spain":   169,
    # Europa — sin verificar
    "hamburg":       36,
    "vienna":        32,
    "brussels":      22,
    "prague":        86,
    "budapest":      104,
    "stockholm":     84,
    # Americas — verificados ✅
    "buenos aires":  395,
    "rio de janeiro": 401,
    # Americas — sin verificar
    "new york":      8,
    "los angeles":   2,
    "chicago":       17,
    "miami":         26,
    "sao paulo":     55,
    "mexico city":   411,
    "bogota":        473,
    "santiago":      387,
    "toronto":       71,
    "montreal":      75,
    # Asia / Oceania — sin verificar
    "tokyo":         58,
    "melbourne":     63,
    "sydney":        64,
    "seoul":         94,
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
query GET_ARTIST_EVENTS($slug: String!) {
  artist(slug: $slug) {
    id
    name
    contentUrl
    followerCount
  }
}
"""

# Próximos eventos de un artista (confirmado vía /schema?type=Artist: artist.events(type, limit, areaId))
RA_QUERY_ARTISTA_PROXIMOS = """
query GET_ARTIST_UPCOMING($slug: String!, $limit: Int, $areaId: Int) {
  artist(slug: $slug) {
    id
    name
    upcomingEventsCount
    events(type: LATEST, limit: $limit, areaId: $areaId) {
      id title date startTime endTime contentUrl cost attending
      venue { name address area { name id } }
      artists { name }
      pick { blurb }
    }
  }
}
"""

# Query busqueda general
RA_QUERY_ARTIST_SEARCH = """
query SEARCH_ARTISTS($query: String!) {
  artistSearch(query: $query) {
    id
    name
    contentUrl
    followerCount
  }
}
"""

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

REGLA MÁS IMPORTANTE: Procesá TODA la info que el usuario ya dio antes de hacer preguntas. Si dijo "planes este finde en Berlin noche", ya tenés ciudad=Berlin, cuando=todo, hora=22:00 — no preguntes nada, buscá directamente.

Podés buscar eventos de tres formas:
1. Por fecha + ciudad
2. Por artista/DJ
3. Por venue/disco

FLUJO para FECHA+CIUDAD — necesitás exactamente estos 4 datos:
- Ciudad: solo ciudades del listado (london, berlin, barcelona, madrid, amsterdam, paris, ibiza, rome, lisbon, vienna, prague, budapest, stockholm, brussels, hamburg, new york, los angeles, chicago, miami, buenos aires, sao paulo, mexico city, bogota, santiago, toronto, montreal, tokyo, melbourne, sydney, seoul). Si el usuario escribe algo parecido (lonbon→london, barcleona→barcelona) corregí vos sin preguntar.
- Cuándo: "este finde"/"finde"/"fin de semana" = "todo". "viernes" = "viernes". "sábado"/"sabado" = "sabado". "domingo"/"dom" = "domingo". Si es vago preguntá.
- Horario (franja de INICIO del evento): de día = hora_min "10:00" y hora_max "17:59"; tarde = hora_min "14:00" y hora_max "20:59"; noche = hora_min "21:00" sin hora_max; madrugada = hora_min "00:00" y hora_max "06:00"; me da igual = sin hora_min ni hora_max. Si no lo menciona preguntá UNA VEZ.
- Precio: gratis, barato<15€, normal 15-30€, caro>30€, da igual. Si no lo menciona preguntá UNA VEZ.

IMPORTANTE: NO preguntes por "lugar cubierto/aire libre" — esa función no está disponible todavía.
NO repitas preguntas que el usuario ya respondió en la misma conversación.
Si el usuario responde "sí" a "¿en qué ciudad?" significa que confirmó la ciudad anterior, no que su ciudad se llama "sí".

FLUJO para ARTISTA:
- Nombre del artista (corregí errores tipográficos vos)
- Ciudad (opcional) — si no da ciudad usá "todas" en el JSON
- NO preguntes ciudad si el usuario claramente quiere ver TODAS las fechas

FLUJO para VENUE/DISCO:
- Nombre del venue (corregí errores vos)
- Ciudad (opcional)
- Cuándo (preguntá si no lo dijo)

Cuando tengas suficiente info, escribí este bloque al final:
###FILTROS###
{
  "tipo": "fecha",
  "ciudad": "barcelona",
  "cuando": "sabado",
  "hora_min": "22:00",
  "hora_max": null,
  "lugar": "cubierto",
  "precio_max": 30,
  "gratis": false,
  "max": 8
}
###FIN###

O para artista (con ciudad):
###FILTROS###
{
  "tipo": "artista",
  "artista": "amelie lens",
  "ciudad": "barcelona",
  "cuando": "todo",
  "max": 8
}
###FIN###

O para artista (sin ciudad — busca en todas):
###FILTROS###
{
  "tipo": "artista",
  "artista": "amelie lens",
  "ciudad": "todas",
  "cuando": "todo",
  "max": 8
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
- Siempre escribí "venue/disco" cuando te refieras a un lugar.
- El usuario puede llegar con mensajes predeterminados de la pantalla de inicio. Interpretálos así:
  * "Quiero planes para este finde" → preguntá ciudad directamente
  * "Busco algo gratis o barato este finde" → preguntá ciudad, luego buscá con gratis=true o precio_max=15
  * "Quiero algo de día o tarde, open air o sunset" → preguntá ciudad, luego buscá con hora_min="14:00" y hora_max="20:59"
  * "Quiero buscar las próximas fechas de un DJ o artista" → preguntá el nombre del artista
  * "Quiero saber qué hay en una disco o venue en particular" → preguntá el nombre del venue/disco y ciudad
  * "Quiero buscar eventos por género musical" → preguntá qué género y ciudad
- IMPORTANTE: el filtro de "aire libre" o "cubierto" todavía no está disponible. Si el usuario lo pide, avisale: "Por ahora no puedo filtrar por aire libre/cubierto automáticamente — esa función viene pronto! Mientras tanto busco por fecha y ciudad y vos elegís el que más te gusta." Luego continuá con la búsqueda normal sin ese filtro y NO lo incluyas en el JSON de filtros."""

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

RA_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://ra.co",
    "Referer": "https://ra.co/events",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

def ra_request(payload):
    """Request directo a RA sin proxy"""
    resp = requests.post(RA_GRAPHQL_URL, headers=RA_HEADERS, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()

def ra_request_direct(payload):
    """Para artistas — directo sin proxy, más rápido"""
    resp = requests.post(RA_GRAPHQL_URL, headers=RA_HEADERS, json=payload, timeout=20)
    resp.raise_for_status()
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
    if any(x in c for x in ["domingo", "sunday", "dom"]):
        return "domingo"
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
    if cuando == "domingo": return fmt(dom),  fmt(dom)
    return fmt(vier), fmt(dom)  # todo = viernes + sábado + domingo

def precio_categoria(cost_str):
    """Convierte string de precio al mínimo — para mostrar 'desde X€'"""
    if not cost_str or cost_str.strip() == "":
        return None
    cost = cost_str.strip()
    if cost.lower() in ["free", "gratis", "0", "€", "£", "$"]:
        return 0
    # Extraer todos los números del string
    nums = re.findall(r'\d+(?:[.,]\d+)?', cost)
    if not nums:
        return None
    # Convertir a float y devolver el mínimo (precio más barato)
    valores = []
    for n in nums:
        try:
            valores.append(float(n.replace(',', '.')))
        except:
            pass
    return int(min(valores)) if valores else None

def formatear_evento(ev, venue_data=None):
    venue = ev.get("venue") or {}
    cost  = (ev.get("cost") or "").strip()
    hora  = (ev.get("startTime") or "")[11:16]
    artistas = [a.get("name", "") for a in ev.get("artists", [])]
    precio_num = precio_categoria(cost)

    # Símbolo de moneda
    tiene_simbolo = any(s in cost for s in ['£', '$', '€'])
    sym = '' if tiene_simbolo else '€'

    if precio_num == 0:
        precio_label = "Gratis"
        es_gratis = True
    elif precio_num is not None and precio_num < 15:
        precio_label = f"{sym}{precio_num} · Barato"
        es_gratis = False
    elif precio_num is not None and precio_num <= 30:
        precio_label = f"{sym}{precio_num} · Normal"
        es_gratis = False
    elif precio_num is not None:
        precio_label = f"{sym}{precio_num} · Caro"
        es_gratis = False
    else:
        precio_label = "Ver precio en RA"
        es_gratis = False

    return {
        "titulo":       ev.get("title", ""),
        "fecha":        (ev.get("date") or "")[:10],
        "hora":         hora,
        "hora_fin":     (ev.get("endTime") or "")[11:16],
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

def buscar_por_fecha(ciudad, desde, hasta, max_ev=8, hora_min=None, gratis=False, precio_max=None, hora_max=None):
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
            if hora_max:
                # Franja cerrada (día/tarde/madrugada): la hora de inicio tiene que caer dentro
                if not hora or not ((hora_min or "00:00") <= hora <= hora_max):
                    continue
            elif hora_min and hora and hora >= "08:00" and hora < hora_min:
                # Noche: los eventos que arrancan después de medianoche también valen
                continue
            # Filtro gratis
            if gratis and not ev_fmt["gratis"]:
                continue
            # Filtro precio máximo
            if precio_max is not None and ev_fmt["precio_num"] is not None and ev_fmt["precio_num"] > precio_max:
                continue

            eventos.append(ev_fmt)

        # Filtrar eventos pasados
        hoy = datetime.now().strftime("%Y-%m-%d")
        eventos = [e for e in eventos if e.get("fecha", "") >= hoy]

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
    print(f"[FILTROS] {filtros}")

    eventos_out = []
    total_ra    = 0

    if filtros:
        tipo = filtros.get("tipo", "fecha")
        ciudad_raw = filtros.get("ciudad", "berlin")

        # Para búsqueda por artista la ciudad es opcional
        tipo = filtros.get("tipo", "fecha")
        if ciudad_raw.lower() in ["todas", "all", "cualquiera", ""]:
            ciudad_corregida = "barcelona"  # default, no se usa para artista
            ciudad_ok = True
        else:
            # Corrección fuzzy de ciudad
            ciudad_corregida, ciudad_ok = corregir_ciudad(ciudad_raw)
            if not ciudad_ok and tipo != "artista":
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
                hora_max   = filtros.get("hora_max"),
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
            artista_raw = (filtros.get("artista") or filtros.get("query") or "").strip()
            if not artista_raw:
                return jsonify({"reply": "¿Qué artista o DJ querés buscar?", "filtros": None, "eventos": [], "total_ra": 0})
            max_ev_artista = filtros.get("max", 10)
            print(f"[ARTISTA] buscando '{artista_raw}' en ciudades principales")

            try:
                hoy_str = datetime.now().strftime("%Y-%m-%d")
                artista_lower = artista_raw.lower().strip()

                # Primero buscar el artista en RA para obtener el slug correcto
                slugs_a_probar = []
                try:
                    search_payload = {
                        "operationName": "SEARCH_ARTISTS",
                        "variables": {"query": artista_raw},
                        "query": RA_QUERY_ARTIST_SEARCH
                    }
                    search_data = ra_request_direct(search_payload)
                    artistas_encontrados = search_data.get("data", {}).get("artistSearch", [])
                    print(f"[ARTISTA] Búsqueda encontró: {[a.get('name') for a in artistas_encontrados[:3]]}")
                    for a in artistas_encontrados[:3]:
                        content_url = a.get("contentUrl", "")
                        slug_ra = content_url.replace("/dj/", "").strip("/")
                        if slug_ra:
                            slugs_a_probar.append(slug_ra)
                except Exception as se:
                    print(f"[ARTISTA SEARCH ERROR] {se}")

                # Agregar variantes generadas como fallback
                slug_guion   = re.sub(r'[^a-z0-9]+', '-', artista_lower).strip('-')
                slug_sin_esp = re.sub(r'[^a-z0-9]+', '',  artista_lower)
                for s in [slug_guion, slug_sin_esp]:
                    if s not in slugs_a_probar:
                        slugs_a_probar.append(s)

                slugs = slugs_a_probar

                # Ciudad opcional: si viene una ciudad válida, filtramos por área de RA
                area_id = None
                ciudad_art = (filtros.get("ciudad") or "").lower().strip()
                if ciudad_art and ciudad_art not in ["todas", "all", "cualquiera"]:
                    area_id = AREAS_RA.get(ciudad_corregida)

                # Probar slugs hasta encontrar el artista y traer sus próximos eventos
                artist_info = None
                nombre_real = artista_raw.title()
                for slug in slugs:
                    print(f"[ARTISTA] Probando slug: '{slug}'")
                    data_art = ra_request_direct({
                        "operationName": "GET_ARTIST_UPCOMING",
                        "variables": {"slug": slug, "limit": 50, "areaId": area_id},
                        "query": RA_QUERY_ARTISTA_PROXIMOS,
                    })
                    if data_art.get("errors"):
                        print(f"[ARTISTA ERRORS] {str(data_art.get('errors'))[:300]}")
                    candidate = (data_art.get("data") or {}).get("artist")
                    if candidate and candidate.get("id"):
                        artist_info = candidate
                        nombre_real = candidate.get("name") or nombre_real
                        print(f"[ARTISTA] Encontrado: {nombre_real} (id={candidate.get('id')}, próximos={candidate.get('upcomingEventsCount')})")
                        break

                if artist_info:
                    eventos_out = []
                    for ev in (artist_info.get("events") or []):
                        if not ev:
                            continue
                        ev_fmt = formatear_evento(ev)
                        if ev_fmt.get("fecha", "") >= hoy_str:
                            eventos_out.append(ev_fmt)
                    eventos_out.sort(key=lambda x: x.get("fecha", ""))

                    if eventos_out:
                        total_ra    = artist_info.get("upcomingEventsCount") or len(eventos_out)
                        eventos_out = eventos_out[:max_ev_artista]
                        clean = f"Próximas fechas de {nombre_real}:"
                    else:
                        donde = f" en {ciudad_corregida.title()}" if area_id else ""
                        return jsonify({
                            "reply": f"No encontré próximas fechas de {nombre_real}{donde} en RA por el momento.",
                            "filtros": filtros, "eventos": [], "total_ra": 0
                        })
                else:
                    return jsonify({
                        "reply": f"No encontré a '{artista_raw}' en RA. ¿Me pasás el nombre completo como figura en RA? (ej: 'Hernan Cattaneo')",
                        "filtros": filtros, "eventos": [], "total_ra": 0
                    })
            except Exception as e:
                print(f"[ARTISTA ERROR] {e}")
                return jsonify({"error": str(e)}), 500

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
    hora_max  = request.args.get("hora_max")
    gratis    = request.args.get("gratis") == "true"
    precio_max = int(request.args.get("precio_max", 9999))
    max_ev    = int(request.args.get("max", 8))

    if not desde or not hasta:
        desde, hasta = finde_proximo()

    if ciudad not in AREAS_RA:
        return jsonify({"error": f"Ciudad '{ciudad}' no encontrada"}), 400

    evs, total = buscar_por_fecha(ciudad, desde, hasta, max_ev, hora_min, gratis, precio_max, hora_max)
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

@app.route("/schema")
def schema():
    """Inspecciona un tipo del schema GraphQL de RA. Uso: /schema?type=FilterInputDtoInput"""
    type_name = request.args.get("type", "FilterInputDtoInput")
    q = """query($name: String!) { __type(name: $name) { name kind
        enumValues { name }
        inputFields { name type { name kind ofType { name kind } } }
        fields { name args { name type { name kind ofType { name } } } type { name kind ofType { name } } } } }"""
    try:
        return jsonify(ra_request({"query": q, "variables": {"name": type_name}}))
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/debug-genre")
def debug_genre():
    """Prueba cómo filtrar por género en RA. Uso: /debug-genre?g=techno&ciudad=barcelona"""
    g      = request.args.get("g", "techno")
    area   = AREAS_RA.get(request.args.get("ciudad", "barcelona"), 20)
    desde, hasta = finde_proximo()
    out = {}
    # 1) ¿Event tiene campo genres?
    try:
        t = ra_request({"query": '{ __type(name: "Event") { fields { name } } }'})
        campos = [f["name"] for f in ((t.get("data") or {}).get("__type") or {}).get("fields") or []]
        out["event_genre_fields"] = [c for c in campos if "genre" in c.lower()]
        t2 = ra_request({"query": '{ __type(name: "StringFilterInputDtoInput") { inputFields { name } } }'})
        out["string_filter_ops"] = [f["name"] for f in ((t2.get("data") or {}).get("__type") or {}).get("inputFields") or []]
    except Exception as e:
        out["schema_error"] = str(e)
    # 2) Probar el filtro genre con distintas variantes de valor
    q = """query Q($filters: FilterInputDtoInput) { eventListings(filters: $filters, pageSize: 5, page: 1) {
        data { event { title genres { id name } } } totalResults } }"""
    base = {"areas": {"eq": area}, "listingDate": {"gte": f"{desde}T00:00:00.000Z", "lte": f"{hasta}T23:59:59.000Z"}}
    pruebas = {"sin_filtro": None, "eq": {"eq": g}, "eq_lower": {"eq": g.lower()}, "any": {"any": [g]}}
    for nombre, filtro in pruebas.items():
        f = dict(base)
        if filtro: f["genre"] = filtro
        try:
            r = ra_request({"query": q, "variables": {"filters": f}})
            el = (r.get("data") or {}).get("eventListings") or {}
            out[nombre] = {
                "total": el.get("totalResults"),
                "ejemplos": [(d.get("event") or {}) for d in (el.get("data") or [])][:3],
                "errors": str(r.get("errors"))[:300] if r.get("errors") else None,
            }
        except Exception as e:
            out[nombre] = {"error": str(e)[:300]}
    return jsonify(out)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
