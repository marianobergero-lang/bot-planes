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
TM_API_KEY      = os.environ.get("TICKETMASTER_API_KEY", "")
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
        genres { name }
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
      genres { name }
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

SYSTEM_PROMPT = """Sos Let's PartIA, un amigo que conoce toda la movida nocturna y de clubs. Hablás en español rioplatense, cálido, breve y con onda, sin exagerar.

REGLA DE ORO: ENTENDÉ TODO LO QUE EL USUARIO YA DIJO Y NO LO VUELVAS A PREGUNTAR.
Antes de responder, extraé de TODA la conversación: ciudad, cuándo, franja, precio, género, artista, venue.
Para buscar por fecha solo hacen falta DOS datos: CIUDAD y CUÁNDO. Todo lo demás es opcional.
- Si ya tenés ciudad y cuándo → BUSCÁ YA (mandá el bloque de filtros). No preguntes franja, precio ni género: si no los dijo, van en null y el usuario después puede afinar.
- Si falta uno de los dos → preguntá SOLO ese, en una frase corta. Nunca hagas listas de preguntas ni numeres preguntas.
- Si falta todo → preguntá primero la ciudad.

Ejemplos:
- "quiero salir por rio de janeiro en dos semanas" → ciudad=rio de janeiro, cuando=la fecha de HOY + 14 días (YYYY-MM-DD). BUSCÁ YA.
- "techno en berlin el sábado" → ciudad=berlin, cuando=sabado, genero=techno. BUSCÁ YA.
- "algo gratis este finde a la tarde" → falta ciudad: "¡Dale! ¿En qué ciudad?"
- "planes en madrid" → falta cuándo: "¿Para cuándo? ¿Hoy, este finde o alguna fecha en particular?"

DATOS:
- Ciudad: CIUDADES_DISPONIBLES. Corregí errores de tipeo vos (lonbon→london, rio→rio de janeiro, bsas→buenos aires, nueva york→new york). En el JSON usá el nombre tal cual de la lista. Si la ciudad no está en la lista, igual mandá el nombre y el sistema avisará.
- Cuándo (campo "cuando"): "hoy", "mañana", un día de la semana ("lunes"... "domingo", el más cercano), "el otro jueves" (semana siguiente), "esta semana", "finde" (viernes a domingo), o fecha exacta "YYYY-MM-DD". Para "en dos semanas", "en 10 días", "el 25", "jueves 25", etc. calculá la fecha con la fecha de HOY y mandá YYYY-MM-DD. Si el mensaje trae fechas entre paréntesis del calendario, como "(2026-10-13/2026-10-15)" o "(2026-10-25)", poné EXACTAMENTE eso en "cuando". Si el día de la semana no coincide con el número (ej: "jueves 25" y el 25 es domingo), preguntá cuál quiso decir.
- Franja (opcional, según a qué hora EMPIEZA): "tarde" (14-20hs, incluye "de día"), "sunset" (18-21hs), "noche" (21-03hs, incluye madrugada), "afters" (05-09hs), o null.
- Precio (opcional, precio_max SIEMPRE en euros, el sistema convierte la moneda local de cada ciudad): gratis → "gratis": true; barato → "precio_max": 15; normal → "precio_max": 30; si dice un monto en otra moneda, convertilo aproximado a euros; si no dijo → null.
- Género (opcional, en minúscula): techno, hard techno, house, tech house, deep house, afro house, progressive house, melodic, minimal, electro, disco, trance, drum & bass, hip-hop, ambient, garage, jazz. Resident Advisor es sobre todo electrónica: si pide reggaeton, cumbia, rock o pop, avisá que puede haber pocos resultados y buscá igual.
- Categoría (campo "categoria"): "clubs" (fiestas, clubs, electrónica, DJs; es el valor por defecto), "conciertos" (bandas, recitales, shows de música en vivo de rock, pop, latino, etc.), "teatro" (teatro, musicales, comedia, danza, shows), "deportes" (fútbol, básquet, partidos). Si no queda claro, usá "clubs".
- "Lo más top": si pide lo más top, lo que más está pegando o lo más popular, buscá normal (los resultados ya vienen ordenados por cuánta gente va).
- NO hay filtro de aire libre/cubierto todavía. Si lo pide, decí en una línea que viene pronto y buscá igual sin ese filtro.

ARTISTA/DJ: alcanza con el nombre (corregí errores de tipeo). Ciudad opcional: si no la dio, usá "todas". No preguntes nada más.
VENUE/DISCO: nombre del venue y ciudad; si no dijo cuándo, usá "todo".
Si el usuario responde "sí" a una confirmación, se refiere al dato anterior.

FORMATO DE TUS MENSAJES:
- Máximo 2 líneas. Una sola pregunta por mensaje, como máximo.
- Texto plano: NUNCA uses asteriscos, negritas, markdown, encabezados ni listas numeradas.
- Podés usar algún emoji, sin abusar.
- Cuando buscás, escribí solo una frase corta tipo "¡Voy a buscar! 🔎" y después el bloque.

Cuando tengas los datos, escribí este bloque al final:
###FILTROS###
{"tipo": "fecha", "categoria": "clubs", "ciudad": "barcelona", "cuando": "sabado", "franja": null, "precio_max": null, "gratis": false, "genero": null, "max": 8}
###FIN###

Artista:
###FILTROS###
{"tipo": "artista", "artista": "amelie lens", "ciudad": "todas", "cuando": "todo", "max": 8}
###FIN###

Venue/disco:
###FILTROS###
{"tipo": "venue", "venue": "Les Enfants Brillants", "ciudad": "barcelona", "cuando": "todo", "max": 8}
###FIN###

Evento específico por nombre:
###FILTROS###
{"tipo": "busqueda", "query": "Brunch Elektronik", "ciudad": "barcelona", "max": 5}
###FIN###

Mensajes de la pantalla de inicio:
- "Quiero planes para este finde" → cuando=finde, preguntá solo la ciudad.
- "Busco algo gratis o barato este finde" → cuando=finde, precio_max=15, preguntá solo la ciudad.
- "Quiero algo de día o tarde, open air o sunset" → franja=tarde, preguntá ciudad y, si no lo dijo, cuándo.
- "Quiero ver lo más top de hoy" → cuando=hoy, preguntá solo la ciudad.
- "Quiero buscar las próximas fechas de un DJ o artista" → preguntá el nombre.
- "Quiero saber qué hay en una disco o venue en particular" → preguntá el nombre del venue/disco y la ciudad.
- "Quiero buscar eventos por género musical" → preguntá qué género; después ciudad y cuándo si faltan.
- "Quiero ver festivales" → preguntá ciudad y cuándo; buscá tipo "fecha".""".replace(
    "CIUDADES_DISPONIBLES", ", ".join(AREAS_RA.keys()))

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

DIAS_SEMANA = {
    "lunes": 0, "monday": 0, "martes": 1, "tuesday": 1,
    "miercoles": 2, "miércoles": 2, "wednesday": 2,
    "jueves": 3, "thursday": 3, "viernes": 4, "friday": 4,
    "sabado": 5, "sábado": 5, "saturday": 5, "domingo": 6, "sunday": 6,
}

def _hoy():
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

def finde_proximo():
    """Viernes a domingo de este finde. Si ya estamos en el finde, arranca hoy."""
    hoy = _hoy()
    wd = hoy.weekday()
    if wd >= 4:  # viernes, sábado o domingo: el finde en curso
        inicio, fin = hoy, hoy + timedelta(days=6 - wd)
    else:
        inicio = hoy + timedelta(days=4 - wd)
        fin = inicio + timedelta(days=2)
    return inicio.strftime("%Y-%m-%d"), fin.strftime("%Y-%m-%d")

def calc_fechas(cuando):
    """
    Convierte "cuando" en (desde, hasta):
    - fecha exacta "2026-10-15" (o un rango "2026-10-15/2026-10-18")
    - "hoy", "mañana", "esta semana"
    - un día de la semana: el más cercano, contando hoy ("jueves" un jueves = hoy)
    - "el otro jueves" / "jueves que viene" / "siguiente jueves": el de la semana siguiente
    - cualquier otra cosa (finde, todo, weekend): este finde
    """
    fmt = lambda d: d.strftime("%Y-%m-%d")
    c = (cuando or "").lower().strip()
    hoy = _hoy()

    fechas = re.findall(r"\d{4}-\d{2}-\d{2}", c)
    if fechas:
        return fechas[0], fechas[-1]
    # "en dos semanas", "en 10 días", "dentro de una semana"
    numeros = {"un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
               "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "quince": 15}
    m = re.search(r"(?:en|dentro de)\s+(\d+|[a-z]+)\s+(semana|dia|día)", c)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else numeros.get(m.group(1))
        if n:
            d = hoy + timedelta(days=n * (7 if m.group(2) == "semana" else 1))
            return fmt(d), fmt(d)
    # "el 25", "jueves 25", "25/10", "25 de octubre": manda el número (próxima vez que llega ese día)
    m = re.search(r"\b(\d{1,2})(?:\s*(?:/|-|de)\s*(\d{1,2}|[a-z]+))?\b", c)
    if m and 1 <= int(m.group(1)) <= 31:
        dia = int(m.group(1))
        meses = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto",
                 "septiembre","octubre","noviembre","diciembre"]
        mes_txt = m.group(2)
        mes = None
        if mes_txt:
            if mes_txt.isdigit() and 1 <= int(mes_txt) <= 12:
                mes = int(mes_txt)
            else:
                mes = next((i + 1 for i, n in enumerate(meses) if n.startswith(mes_txt[:3])), None)
        for salto in range(0, 13):
            y = hoy.year + (hoy.month - 1 + salto) // 12
            mm = (hoy.month - 1 + salto) % 12 + 1
            if mes and mm != mes:
                continue
            try:
                d = datetime(y, mm, dia)
            except ValueError:
                continue
            if d >= hoy:
                return fmt(d), fmt(d)
    if "pasado mañana" in c or "pasado manana" in c:
        d = hoy + timedelta(days=2); return fmt(d), fmt(d)
    if "mañana" in c or "manana" in c or "tomorrow" in c:
        d = hoy + timedelta(days=1); return fmt(d), fmt(d)
    if "hoy" in c or "today" in c or "esta noche" in c or "tonight" in c:
        return fmt(hoy), fmt(hoy)
    if "semana" in c and "fin de semana" not in c:
        return fmt(hoy), fmt(hoy + timedelta(days=6 - hoy.weekday()))

    siguiente = any(x in c for x in ["otro", "que viene", "siguiente", "next"])
    for nombre, wd in DIAS_SEMANA.items():
        if re.search(rf"\b{nombre}\b", c):
            d = hoy + timedelta(days=(wd - hoy.weekday()) % 7)
            if siguiente:
                d += timedelta(days=7)
            return fmt(d), fmt(d)

    desde, hasta = finde_proximo()
    if siguiente:  # "el finde que viene"
        desde = fmt(datetime.strptime(desde, "%Y-%m-%d") + timedelta(days=7 if hoy.weekday() < 4 else 0))
        desde, hasta = (fmt(datetime.strptime(desde, "%Y-%m-%d")), fmt(datetime.strptime(desde, "%Y-%m-%d") + timedelta(days=2)))
    return desde, hasta

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

# Moneda local por ciudad (RA da el precio en moneda local y sin símbolo)
# Cotizaciones aproximadas a euros: solo para clasificar barato/normal/caro
MONEDAS = {
    "EUR": ("€", 1.0),      "GBP": ("£", 1.17),      "USD": ("US$", 0.92),
    "BRL": ("R$", 0.17),    "ARS": ("AR$", 0.0008),  "MXN": ("MX$", 0.05),
    "COP": ("COL$", 0.00022), "CLP": ("CLP$", 0.00097), "CAD": ("CA$", 0.67),
    "JPY": ("¥", 0.0061),   "AUD": ("AU$", 0.60),    "KRW": ("₩", 0.00066),
    "CZK": ("Kč", 0.040),   "HUF": ("Ft", 0.0025),   "SEK": ("kr", 0.087),
    "CHF": ("CHF", 1.05),
}
MONEDA_POR_CIUDAD = {
    "london": "GBP", "new york": "USD", "los angeles": "USD", "chicago": "USD", "miami": "USD",
    "rio de janeiro": "BRL", "sao paulo": "BRL", "são paulo": "BRL", "buenos aires": "ARS",
    "mexico city": "MXN", "bogota": "COP", "bogotá": "COP", "santiago": "CLP",
    "toronto": "CAD", "montreal": "CAD", "tokyo": "JPY", "melbourne": "AUD", "sydney": "AUD",
    "seoul": "KRW", "prague": "CZK", "budapest": "HUF", "stockholm": "SEK",
    "zurich": "CHF", "geneva": "CHF",
}
MONEDA_POR_AREA = {AREAS_RA[c]: m for c, m in MONEDA_POR_CIUDAD.items() if c in AREAS_RA}

def moneda_evento(ev, cost):
    """Devuelve el código de moneda del evento: símbolo explícito > área > nombre de ciudad > EUR"""
    if "£" in cost: return "GBP"
    if "€" in cost: return "EUR"
    area = ((ev.get("venue") or {}).get("area") or {})
    try:
        m = MONEDA_POR_AREA.get(int(area.get("id") or 0))
    except (TypeError, ValueError):
        m = None
    if not m:
        nombre = (area.get("name") or "").lower()
        m = next((v for k, v in MONEDA_POR_CIUDAD.items() if k in nombre), None)
    if m:
        return m
    return "USD" if "$" in cost else "EUR"

def formatear_evento(ev, venue_data=None):
    venue = ev.get("venue") or {}
    cost  = (ev.get("cost") or "").strip()
    hora  = (ev.get("startTime") or "")[11:16]
    artistas = [a.get("name", "") for a in ev.get("artists", [])]
    precio_local = precio_categoria(cost)

    moneda = moneda_evento(ev, cost)
    sym, a_eur = MONEDAS.get(moneda, ("€", 1.0))
    # precio_num queda SIEMPRE en euros (lo usan los filtros de precio)
    precio_num = None if precio_local is None else round(precio_local * a_eur)
    if precio_local is not None and precio_local > 0 and precio_num == 0:
        precio_num = 1
    precio_txt = f"{sym}{precio_local:,}".replace(",", ".") if precio_local else ""
    if sym.isalpha():  # Kč, Ft, kr, CHF van con espacio
        precio_txt = f"{precio_local:,} {sym}".replace(",", ".") if precio_local else ""

    es_gratis = precio_local == 0
    if es_gratis:
        precio_cat, precio_label = "gratis", "Gratis"
    elif precio_num is None:
        precio_cat, precio_label = "nd", "Ver precio en RA"
    elif precio_num < 15:
        precio_cat, precio_label = "barato", f"Barato · {precio_txt}"
    elif precio_num <= 30:
        precio_cat, precio_label = "normal", f"Normal · {precio_txt}"
    else:
        precio_cat, precio_label = "caro", f"Caro · {precio_txt}"

    return {
        "moneda":       moneda,
        "precio_cat":   precio_cat,
        "titulo":       ev.get("title", ""),
        "fecha":        (ev.get("date") or "")[:10],
        "hora":         hora,
        "hora_fin":     (ev.get("endTime") or "")[11:16],
        "venue":        venue.get("name", ""),
        "direccion":    venue.get("address", ""),
        "ciudad_venue": (venue.get("area") or {}).get("name", ""),
        "artistas":     artistas,
        "generos":      [g.get("name", "") for g in (ev.get("genres") or []) if g],
        "precio":       cost or "No especificado",
        "precio_label": precio_label,
        "precio_num":   precio_num,
        "gratis":       es_gratis,
        "asistentes":   ev.get("attending", 0),
        "destacado":    (ev.get("pick") or {}).get("blurb", ""),
        "url":          f"https://ra.co{ev.get('contentUrl', '')}",
        "fuente":       "Resident Advisor",
    }

# Géneros: RA filtra con genre: {any: [...]} (verificado con /debug-genre: "techno" → 32 de 120 eventos en BCN)
GENEROS_ALIAS = {
    "tecno": "techno", "techno": "techno", "hard techno": "hard techno",
    "house": "house", "tech house": "tech house", "deep house": "deep house",
    "afro house": "afro house", "progressive": "progressive house", "progresivo": "progressive house",
    "progressive house": "progressive house", "melodic": "melodic house & techno",
    "minimal": "minimal", "electro": "electro", "disco": "disco", "trance": "trance",
    "drum&bass": "drum & bass", "drum and bass": "drum & bass", "dnb": "drum & bass", "d&b": "drum & bass",
    "hip hop": "hip-hop", "hiphop": "hip-hop", "rap": "hip-hop", "bass": "bass", "dubstep": "dubstep",
    "ambient": "ambient", "experimental": "experimental", "breaks": "breakbeat", "garage": "garage",
    "jungle": "jungle", "jazz": "jazz", "funk": "funk", "soul": "soul", "pop": "pop", "rock": "rock",
    "indie": "indie", "reggaeton": "reggaeton", "latin": "latin", "cumbia": "latin",
}

def variantes_genero(genero):
    """Devuelve variantes del nombre del género para el filtro any de RA"""
    g = (genero or "").lower().strip()
    if not g or g in ["todos", "cualquiera", "da igual", "me da igual"]:
        return None
    base = GENEROS_ALIAS.get(g, g)
    vs = {base, base.replace(" ", "-"), base.replace("&", "and"), base.replace(" & ", "-and-"), base.replace(" ", "")}
    return sorted(vs)

# Franjas por hora de INICIO del evento (se pisan a propósito)
FRANJAS = {
    "tarde":  ("14:00", "20:00"),
    "sunset": ("18:00", "21:00"),
    "noche":  ("21:00", "03:00"),
    "afters": ("05:00", "09:00"),
}

def normalizar_franja(franja):
    f = (franja or "").lower().strip()
    if any(x in f for x in ["after"]):            return "afters"
    if any(x in f for x in ["sunset", "atardecer", "anochecer"]): return "sunset"
    if any(x in f for x in ["noche", "night", "madrugada"]):      return "noche"
    if any(x in f for x in ["tarde", "dia", "día", "day"]):       return "tarde"
    return None

def pasa_filtros(ev_fmt, hora_min=None, hora_max=None, gratis=False, precio_max=None, genero=None):
    """Filtros comunes a todas las fuentes (RA, Ticketmaster...)"""
    hora = ev_fmt.get("hora")
    if hora_min and hora_max:  # franja por hora de INICIO
        if not hora:
            return False
        if hora_min <= hora_max:
            dentro = hora_min <= hora <= hora_max
        else:  # cruza medianoche (noche 21:00 → 03:00)
            dentro = hora >= hora_min or hora <= hora_max
        if not dentro:
            return False
    elif hora_min and hora and hora >= "08:00" and hora < hora_min:
        return False
    if gratis and not ev_fmt.get("gratis"):
        return False
    if precio_max is not None and ev_fmt.get("precio_num") is not None and ev_fmt["precio_num"] > precio_max:
        return False
    if genero:
        g = genero.lower()
        if not any(g in x.lower() or x.lower() in g for x in ev_fmt.get("generos") or []):
            return False
    return True

def repartir_por_dia(eventos, max_ev):
    """Los más populares de cada día, repartidos para que un rango no se llene con el primer día"""
    hoy = datetime.now().strftime("%Y-%m-%d")
    por_dia = {}
    for e in sorted(eventos, key=lambda x: (-(x.get("asistentes") or 0), x.get("relevancia", 0))):
        if e.get("fecha", "") >= hoy:
            por_dia.setdefault(e.get("fecha", ""), []).append(e)
    elegidos, ronda = [], 0
    while len(elegidos) < max_ev and any(len(v) > ronda for v in por_dia.values()):
        for dia in sorted(por_dia):
            if ronda < len(por_dia[dia]) and len(elegidos) < max_ev:
                elegidos.append(por_dia[dia][ronda])
        ronda += 1
    elegidos.sort(key=lambda x: (x.get("fecha", ""), x.get("hora") or "99", -(x.get("asistentes") or 0)))
    return elegidos

def buscar_por_fecha(ciudad, desde, hasta, max_ev=8, hora_min=None, gratis=False, precio_max=None, hora_max=None, genero=None, franja=None):
    f = normalizar_franja(franja)
    if f:
        hora_min, hora_max = FRANJAS[f]
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
                        **({"genre": {"any": variantes_genero(genero)}} if variantes_genero(genero) else {}),
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
            if pasa_filtros(ev_fmt, hora_min, hora_max, gratis, precio_max):
                eventos.append(ev_fmt)

        return repartir_por_dia(eventos, max_ev), total

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

def limpiar_texto(t):
    """Saca markdown (negritas, encabezados) que a veces mete el modelo"""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t or "")
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"^#+\s*", "", t, flags=re.M)
    return t.replace("**", "").strip()

def fecha_de_hoy():
    nombres = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    hoy = datetime.now()
    return f"\n\nHOY es {nombres[hoy.weekday()]} {hoy.strftime('%Y-%m-%d')}."

def call_groq(messages):
    print(f"[GROQ] key: {GROQ_API_KEY[:10]}... msgs: {len(messages)}")
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "openai/gpt-oss-120b",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT + fecha_de_hoy()}] + messages,
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
    clean   = limpiar_texto(clean)
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
                    "reply": f"Uh, todavía no tengo {ciudad_raw.title()} en mi radar 😕 Por ahora busco en ciudades como Barcelona, Madrid, Berlin, London, Paris, Amsterdam, Buenos Aires o Rio de Janeiro. ¿Probamos con alguna?",
                    "filtros": None, "eventos": [], "total_ra": 0
                })
        if ciudad_corregida != ciudad_raw.lower():
            pass

        if tipo == "fecha":
            desde, hasta = calc_fechas(filtros.get("cuando", "todo"))
            n_dias = (datetime.strptime(hasta, "%Y-%m-%d") - datetime.strptime(desde, "%Y-%m-%d")).days + 1
            if n_dias > 1:  # rango: hasta 4 eventos por día (tope 20)
                filtros["max"] = max(filtros.get("max", 8), min(4 * n_dias, 20))
            categoria = (filtros.get("categoria") or "clubs").lower()
            if categoria in TM_SEGMENTOS:
                eventos_out, total_ra = buscar_ticketmaster(
                    ciudad_corregida, desde, hasta, categoria, filtros.get("max", 8),
                    filtros.get("hora_min"), filtros.get("hora_max"), filtros.get("franja"),
                    filtros.get("gratis", False), filtros.get("precio_max"), filtros.get("genero"))
            else:
              eventos_out, total_ra = buscar_por_fecha(
                ciudad     = ciudad_corregida,
                desde      = desde,
                hasta      = hasta,
                max_ev     = filtros.get("max", 8),
                hora_min   = filtros.get("hora_min"),
                hora_max   = filtros.get("hora_max"),
                franja     = filtros.get("franja"),
                gratis     = filtros.get("gratis", False),
                precio_max = filtros.get("precio_max"),
                genero     = filtros.get("genero"),
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
                            "reply": f"Por ahora {nombre_real} no tiene fechas anunciadas{donde} 😕 ¿Probamos sin ciudad o con otro artista?",
                            "filtros": filtros, "eventos": [], "total_ra": 0
                        })
                else:
                    return jsonify({
                        "reply": f"No logro encontrar a {artista_raw} 🤔 ¿Me pasás el nombre completo como figura en Resident Advisor? Por ejemplo: Hernan Cattaneo.",
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
                    "reply": f"No veo nada anunciado en {venue_raw} para esas fechas 😕 ¿Probamos otras fechas o revisamos el nombre del venue/disco?",
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
    franja    = request.args.get("franja")
    gratis    = request.args.get("gratis") == "true"
    precio_max = int(request.args.get("precio_max")) if request.args.get("precio_max") else None
    max_ev    = int(request.args.get("max", 8))
    categoria = request.args.get("categoria", "clubs")
    genero    = request.args.get("genero")

    if not desde or not hasta:
        desde, hasta = finde_proximo()

    if categoria != "clubs":
        evs, total = buscar_ticketmaster(ciudad, desde, hasta, categoria, max_ev, hora_min, hora_max,
                                         franja, gratis, precio_max, genero)
        return jsonify({"ciudad": ciudad, "categoria": categoria, "desde": desde, "hasta": hasta,
                        "total_ra": total, "total": len(evs), "eventos": evs})

    if ciudad not in AREAS_RA:
        return jsonify({"error": f"Ciudad '{ciudad}' no encontrada"}), 400

    evs, total = buscar_por_fecha(ciudad, desde, hasta, max_ev, hora_min, gratis, precio_max, hora_max, franja=franja)
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

TM_URL = "https://app.ticketmaster.com/discovery/v2/events.json"

def tm_buscar(ciudad, pais, desde, hasta, segmento=None, size=100, sort="date,asc"):
    params = {"apikey": TM_API_KEY, "city": ciudad, "startDateTime": desde, "endDateTime": hasta,
              "size": size, "sort": sort, "locale": "*"}
    if pais: params["countryCode"] = pais
    if segmento: params["classificationName"] = segmento
    r = requests.get(TM_URL, params=params, timeout=20)
    return r.status_code, r.json()

# Ciudad (clave de AREAS_RA) → (nombre en Ticketmaster, código de país)
TM_CIUDADES = {
    "london": ("London", "GB"), "berlin": ("Berlin", "DE"), "amsterdam": ("Amsterdam", "NL"),
    "barcelona": ("Barcelona", "ES"), "madrid": ("Madrid", "ES"), "ibiza": ("Ibiza", "ES"),
    "mallorca": ("Palma", "ES"), "valencia": ("Valencia", "ES"), "paris": ("Paris", "FR"),
    "rome": ("Roma", "IT"), "milan": ("Milano", "IT"), "munich": ("München", "DE"),
    "lisbon": ("Lisboa", "PT"), "porto": ("Porto", "PT"), "turin": ("Torino", "IT"),
    "hamburg": ("Hamburg", "DE"), "vienna": ("Wien", "AT"), "brussels": ("Brussels", "BE"),
    "prague": ("Praha", "CZ"), "budapest": ("Budapest", "HU"), "stockholm": ("Stockholm", "SE"),
    "new york": ("New York", "US"), "los angeles": ("Los Angeles", "US"), "chicago": ("Chicago", "US"),
    "miami": ("Miami", "US"), "toronto": ("Toronto", "CA"), "montreal": ("Montreal", "CA"),
    "mexico city": ("Ciudad de México", "MX"), "melbourne": ("Melbourne", "AU"), "sydney": ("Sydney", "AU"),
    "buenos aires": ("Buenos Aires", "AR"), "sao paulo": ("São Paulo", "BR"), "rio de janeiro": ("Rio de Janeiro", "BR"),
    "santiago": ("Santiago", "CL"), "bogota": ("Bogotá", "CO"),
}
# Categoría de la app → segmento de Ticketmaster
TM_SEGMENTOS = {"conciertos": "Music", "teatro": "Arts & Theatre", "deportes": "Sports", "otros": "Miscellaneous"}

def formatear_tm(ev):
    """Evento de Ticketmaster → mismo formato que formatear_evento (RA)"""
    st = (ev.get("dates") or {}).get("start", {})
    venue = ((ev.get("_embedded") or {}).get("venues") or [{}])[0]
    attractions = (ev.get("_embedded") or {}).get("attractions") or []
    cls = (ev.get("classifications") or [{}])[0]
    generos = []
    for x in [(cls.get("genre") or {}).get("name"), (cls.get("subGenre") or {}).get("name")]:
        if x and x not in ("Undefined", "Other", "Music") and x not in generos:
            generos.append(x)
    pr = (ev.get("priceRanges") or [None])[0]
    moneda = (pr or {}).get("currency") or "EUR"
    sym, a_eur = MONEDAS.get(moneda, (moneda + " ", 1.0))
    precio_local = (pr or {}).get("min")
    precio_num = None if precio_local is None else round(precio_local * a_eur)
    if precio_local is None:
        precio_cat, precio_label = "nd", "Ver precio"
    elif precio_local == 0:
        precio_cat, precio_label = "gratis", "Gratis"
    else:
        txt = f"{sym}{int(round(precio_local)):,}".replace(",", ".")
        precio_cat = "barato" if precio_num < 15 else "normal" if precio_num <= 30 else "caro"
        precio_label = f"{precio_cat.capitalize()} · desde {txt}"
    imgs = sorted(ev.get("images") or [], key=lambda i: -(i.get("width") or 0))
    img = next((i.get("url") for i in imgs if (i.get("ratio") == "16_9" and (i.get("width") or 0) <= 1100)), None)
    return {
        "titulo":       ev.get("name", ""),
        "fecha":        st.get("localDate", ""),
        "hora":         (st.get("localTime") or "")[:5],
        "hora_fin":     "",
        "venue":        venue.get("name", ""),
        "direccion":    (venue.get("address") or {}).get("line1", ""),
        "ciudad_venue": (venue.get("city") or {}).get("name", ""),
        "artistas":     [a.get("name", "") for a in attractions][:6],
        "generos":      generos,
        "moneda":       moneda,
        "precio_cat":   precio_cat,
        "precio":       f"{precio_local}" if precio_local is not None else "No especificado",
        "precio_label": precio_label,
        "precio_num":   precio_num,
        "gratis":       precio_local == 0,
        "asistentes":   0,
        "destacado":    (ev.get("info") or ev.get("pleaseNote") or "")[:200],
        "url":          ev.get("url", ""),
        "imagen":       img,
        "fuente":       "Ticketmaster",
        "categoria":    (cls.get("segment") or {}).get("name", ""),
    }

def buscar_ticketmaster(ciudad, desde, hasta, categoria="conciertos", max_ev=8, hora_min=None, hora_max=None,
                        franja=None, gratis=False, precio_max=None, genero=None):
    """Busca en Ticketmaster Discovery API. Devuelve (eventos, total)"""
    if not TM_API_KEY:
        return [], 0
    tm = TM_CIUDADES.get((ciudad or "").lower().strip())
    if not tm:
        return [], 0
    f = normalizar_franja(franja)
    if f:
        hora_min, hora_max = FRANJAS[f]
    # Ticketmaster trabaja en UTC: ampliamos un día a cada lado y después filtramos por fecha local
    d0 = (datetime.strptime(desde, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    d1 = (datetime.strptime(hasta, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59Z")
    try:
        code, data = tm_buscar(tm[0], tm[1], d0, d1, TM_SEGMENTOS.get(categoria), size=200, sort="relevance,desc")
    except Exception as e:
        print(f"[TM ERROR] {e}")
        return [], 0
    if code != 200:
        print(f"[TM ERROR] status={code} {str(data)[:300]}")
        return [], 0
    vistos, eventos = set(), []
    for i, ev in enumerate((data.get("_embedded") or {}).get("events") or []):
        ev_fmt = formatear_tm(ev)
        ev_fmt["relevancia"] = i  # orden de relevancia de Ticketmaster (0 = más relevante)
        if not (desde <= ev_fmt["fecha"] <= hasta):
            continue
        clave = (ev_fmt["titulo"].lower(), ev_fmt["fecha"])  # turnos repetidos del mismo evento
        if clave in vistos:
            continue
        vistos.add(clave)
        if pasa_filtros(ev_fmt, hora_min, hora_max, gratis, precio_max, genero):
            eventos.append(ev_fmt)
    return repartir_por_dia(eventos, max_ev), len(eventos)

@app.route("/debug-tm")
def debug_tm():
    """Cobertura de Ticketmaster por categoría. Uso: /debug-tm?ciudad=Barcelona&pais=ES&dias=14"""
    if not TM_API_KEY:
        return jsonify({"error": "Falta la variable TICKETMASTER_API_KEY en Railway"})
    ciudad = request.args.get("ciudad", "Barcelona")
    pais   = request.args.get("pais", "")
    dias   = int(request.args.get("dias", 14))
    desde  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    hasta  = (datetime.utcnow() + timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {"ciudad": ciudad, "dias": dias, "por_categoria": {}}
    for seg in ["Music", "Sports", "Arts & Theatre", "Miscellaneous"]:
        try:
            code, data = tm_buscar(ciudad, pais, desde, hasta, seg)
        except Exception as e:
            out["por_categoria"][seg] = {"error": str(e)[:200]}; continue
        if code != 200:
            out["por_categoria"][seg] = {"status": code, "respuesta": str(data)[:300]}; continue
        eventos = (data.get("_embedded") or {}).get("events") or []
        vistos, unicos, generos = set(), [], {}
        for ev in eventos:
            nombre = ev.get("name", "")
            if nombre in vistos:  # turnos repetidos del mismo evento
                continue
            vistos.add(nombre)
            cls = (ev.get("classifications") or [{}])[0]
            gen = (cls.get("genre") or {}).get("name", "?")
            generos[gen] = generos.get(gen, 0) + 1
            pr = (ev.get("priceRanges") or [{}])[0]
            venue = ((ev.get("_embedded") or {}).get("venues") or [{}])[0]
            st = (ev.get("dates") or {}).get("start", {})
            unicos.append({"nombre": nombre, "fecha": st.get("localDate"), "hora": (st.get("localTime") or "")[:5],
                           "venue": venue.get("name"), "genero": gen,
                           "precio": f"{pr.get('min')}-{pr.get('max')} {pr.get('currency')}" if pr else None})
        out["por_categoria"][seg] = {
            "total_ticketmaster": (data.get("page") or {}).get("totalElements"),
            "eventos_distintos_en_muestra": len(unicos),
            "con_precio": sum(1 for u in unicos if u["precio"]),
            "generos": dict(sorted(generos.items(), key=lambda x: -x[1])),
            "ejemplos": unicos[:5],
        }
    return jsonify(out)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
