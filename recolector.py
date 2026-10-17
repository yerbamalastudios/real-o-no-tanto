#!/usr/bin/env python3
"""
Pronóstico real o no tanto · programa diario (GitHub Actions).

Baja una vez por día lo que pronostican el SMN, Meteored y AccuWeather (si hay clave)
y lo guarda en datos/sitios.json. La página lo usa para mostrar esos pronósticos y para
ver cuál acierta más. Los modelos globales y las efemérides los consulta la página sola.

Claves como secretos de GitHub: METEORED_KEY (y opcional ACCUWEATHER_KEY).
"""
import io, json, math, os, re, traceback, urllib.error, urllib.parse, urllib.request, zipfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

RAIZ = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(RAIZ, "datos", "sitios.json")
DEBUG_DIR = os.path.join(RAIZ, "datos", "debug")
CFG = {"lugar": "Lomas del Mirador", "lat": -34.66, "lon": -58.53, "zona_horaria": "America/Argentina/Buenos_Aires",
       "smn_estacion_pronostico": ["MORON", "PALOMAR", "AEROPARQUE", "BUENOS_AIRES", "EZEIZA"]}
TZ = ZoneInfo(CFG["zona_horaria"])
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) pronostico-real-o-no-tanto/1.0"}


MESES = {"ENE": 1, "JAN": 1, "FEB": 2, "MAR": 3, "ABR": 4, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7,
         "AGO": 8, "AUG": 8, "SEP": 9, "SET": 9, "OCT": 10, "NOV": 11, "DIC": 12, "DEC": 12}


LINEA_PRON = re.compile(
    r"(\d{1,2})/([A-Za-z]{3})/(\d{4})\s+(\d{1,2})\s*Hs\.?\s+(-?\d+(?:[.,]\d+)?)\s+\d+\s*\|\s*\d+\s+(\d+(?:[.,]\d+)?)")


def http(url, headers=None, binario=False, timeout=40):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cuerpo = r.read()
    except urllib.error.HTTPError as e:
        try:
            detalle = e.read().decode("utf-8", "replace")[:160]
        except Exception:
            detalle = ""
        ruta = re.sub(r"(apikey|apiKey)=[^&]+", r"\1=***", urllib.parse.urlsplit(url).path + "?" + urllib.parse.urlsplit(url).query).rstrip("?")
        raise RuntimeError(f"HTTP {e.code} en {ruta} {detalle}".strip()) from None
    return cuerpo if binario else json.loads(cuerpo.decode("utf-8"))

def leer_json(ruta, defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto


def guardar_json(ruta, obj):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)


def num(v):
    if isinstance(v, dict):
        v = v.get("value", v.get("Value"))
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def dia(d):
    return {"tmax": d.get("tmax"), "tmin": d.get("tmin"), "lluvia": d.get("lluvia"), "prob": d.get("prob")}


def smn_zip(dato):
    crudo = http("https://ssl.smn.gob.ar/dpd/zipopendata.php?dato=" + dato, binario=True, timeout=60)
    try:
        z = zipfile.ZipFile(io.BytesIO(crudo))
        crudo = z.read(z.namelist()[0])
    except zipfile.BadZipFile:
        pass  # a veces viene el txt directo
    return crudo.decode("latin-1")


def parse_pron5d(texto):
    """Devuelve {ESTACION: [(fecha, hora, temp, lluvia), ...]}."""
    estaciones, actual = {}, None
    for linea in texto.splitlines():
        s = linea.strip()
        m = LINEA_PRON.search(s)
        if m and actual:
            dd, mes, aa, hh, t, p = m.groups()
            if mes.upper() not in MESES:
                continue
            f = date(int(aa), MESES[mes.upper()], int(dd))
            estaciones[actual].append((f, int(hh), float(t.replace(",", ".")), float(p.replace(",", "."))))
        elif s and not any(c.isdigit() for c in s) and re.fullmatch(r"[A-ZÁÉÍÓÚÑ_ .()-]{3,}", s) and "=" not in s \
                and "FECHA" not in s and "TEMPERATURA" not in s:
            actual = s.replace(" ", "_")
            estaciones.setdefault(actual, [])
    return {k: v for k, v in estaciones.items() if v}


def elegir(nombres, preferidas):
    for p in preferidas:
        for n in nombres:
            if p.upper().replace(" ", "_") in n.upper().replace(" ", "_"):
                return n
    return None


def asegurar_navegador():
    """Instala el Chrome invisible la primera vez (en GitHub tarda ~1 minuto)."""
    import subprocess, sys
    try:
        import playwright  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "playwright"], check=True)
    subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=True,
                   stdout=subprocess.DEVNULL)


def fuente_smn_api():
    asegurar_navegador()
    from playwright.sync_api import sync_playwright
    base = "https://ws1.smn.gob.ar/v1"
    with sync_playwright() as p:
        nav = p.chromium.launch()
        ctx = nav.new_context(locale="es-AR", user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        pg = ctx.new_page()
        pg.goto("https://www.smn.gob.ar/", wait_until="domcontentloaded", timeout=90000)
        tok = None
        for _ in range(40):
            tok = pg.evaluate("() => window.localStorage.getItem('token')")
            if tok:
                break
            pg.wait_for_timeout(1000)
        if not tok:
            guardar_debug("smn_portada.html", pg.content()[:30000])
            nav.close()
            raise RuntimeError("la web del SMN no entregó el permiso (token)")
        h = {"Authorization": "JWT " + tok, "Accept": "application/json"}

        def pedir(url):
            # Igual que hace la propia web del SMN: el pedido sale desde la página
            try:
                r = pg.evaluate("""async ([u, t]) => { const r = await fetch(u, {headers: {Authorization: 'JWT ' + t}});
                                    return {s: r.status, b: await r.text()}; }""", [url, tok])
                st, cuerpo = r["s"], r["b"]
            except Exception:
                rr = ctx.request.get(url, headers=h, timeout=60000)
                st, cuerpo = rr.status, rr.text()
            if st != 200:
                raise RuntimeError(f"HTTP {st} en {urllib.parse.urlsplit(url).path} {cuerpo[:120]}")
            return json.loads(cuerpo)
        loc = pedir(f"{base}/georef/location/coord?lat={CFG['lat']}&lon={CFG['lon']}")
        guardar_debug("smn_lugar.json", loc)
        ids = buscar(loc, lambda o: isinstance(o, dict) and isinstance(o.get("id"), (int, str)))
        if not ids:
            nav.close()
            raise RuntimeError("el SMN no devolvió una localidad")
        lugar = ids[0]
        j = pedir(f"{base}/forecast/location/{lugar['id']}")
        guardar_debug("smn_pronostico.json", j)
        nav.close()
    serie = extraer_diario(j)
    if not serie:
        raise RuntimeError("no pude leer el pronóstico (ver datos/debug)")
    return {"smn": serie}, lugar.get("name") or str(lugar["id"])


def fuente_smn():
    try:
        return fuente_smn_api()
    except Exception as e1:
        try:
            texto = smn_zip("pron5d")
            est = parse_pron5d(texto)
            if not est:
                raise RuntimeError("archivo vacío")
            nombre = elegir(est, CFG["smn_estacion_pronostico"])
            porDia = {}
            for f, hh, t, p in est[nombre]:
                porDia.setdefault(f.isoformat(), []).append((t, p))
            serie = {f: {"tmax": max(x[0] for x in v), "tmin": min(x[0] for x in v),
                         "lluvia": round(sum(x[1] for x in v), 1), "prob": None}
                     for f, v in porDia.items() if len(v) >= 6}
            return {"smn": serie}, nombre
        except Exception as e2:
            raise RuntimeError(f"API del SMN: {e1} · datos abiertos: {e2}")

def buscar(obj, pred, prof=0):
    """Recorre un JSON y devuelve todos los nodos que cumplen pred."""
    if prof > 12:
        return []
    out = [obj] if pred(obj) else []
    if isinstance(obj, dict):
        for v in obj.values():
            out += buscar(v, pred, prof + 1)
    elif isinstance(obj, list):
        for v in obj:
            out += buscar(v, pred, prof + 1)
    return out


def a_fecha(v):
    if isinstance(v, (int, float)):
        seg = v / 1000 if v > 1e11 else v
        return datetime.fromtimestamp(seg, TZ).date().isoformat()
    if isinstance(v, str):
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", v)
        if m:
            return m.group(0)
        m = re.search(r"(\d{4})(\d{2})(\d{2})", v)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def extraer_diario(j):
    """Lector tolerante: busca una lista de días con máxima y mínima, sin depender del formato exacto."""
    def es_dia(o):
        if not isinstance(o, dict):
            return False
        ks = " ".join(o.keys()).lower()
        return ("max" in ks and "min" in ks) or "temperature" in ks
    serie = {}
    for o in buscar(j, es_dia):
        fecha = None
        for k in ("start", "date", "day", "time", "fecha", "dt", "datetime", "valid"):
            for kk, vv in o.items():
                if kk.lower().startswith(k):
                    fecha = fecha or a_fecha(vv)
        if not fecha:
            continue
        tmax = tmin = lluvia = prob = None
        for kk, vv in o.items():
            k = kk.lower()
            if isinstance(vv, dict) and ("max" in vv or "min" in vv):  # temperature: {min, max}
                tmax = tmax if tmax is not None else num(vv.get("max"))
                tmin = tmin if tmin is not None else num(vv.get("min"))
            elif "max" in k and ("temp" in k or k in ("max", "tmax")):
                tmax = num(vv)
            elif "min" in k and ("temp" in k or k in ("min", "tmin")):
                tmin = num(vv)
            elif ("rain" in k or "precip" in k or "lluvia" in k) and "prob" not in k:
                v = num(vv)
                if v is None and isinstance(vv, dict):
                    v = num(vv.get("total") or vv.get("amount"))
                lluvia = v if lluvia is None else lluvia
            elif "prob" in k:
                prob = num(vv)
        if tmax is not None and tmin is not None and fecha not in serie:
            serie[fecha] = {"tmax": tmax, "tmin": tmin, "lluvia": lluvia, "prob": prob}
    return serie


def fuente_meteored(estado):
    """1 llamada por día (más 1 la primera vez para ubicar el lugar). Límite: 50/día."""
    key = os.environ.get("METEORED_KEY", "").strip()
    if not key:
        return None
    h0 = {"x-api-key": key, "Accept": "application/json"}
    base = "https://api.meteored.com/api"

    def http_met(url, _h=None):
        if LLAMADAS["meteored"] >= MAX_METEORED_TOTAL:
            raise RuntimeError("se llegó al tope diario de consultas a Meteored")
        LLAMADAS["meteored"] += 1
        return http(url, h0)
    h = None
    if not estado.get("meteored_hash"):
        intentos = [f"{base}/location/v1/search/txt/{urllib.parse.quote(CFG['lugar'])}"]
        if CFG.get("provincia"):
            intentos.append(f"{base}/location/v1/search/txt/{urllib.parse.quote(CFG['lugar'] + ', ' + CFG['provincia'])}")
        if CFG.get("cp"):
            intentos.append(f"{base}/location/v1/search/postalcode/{urllib.parse.quote(str(CFG['cp']))}")
        for alt in CFG.get("alternativas") or []:   # si no está, la localidad vecina más cercana
            intentos.append(f"{base}/location/v1/search/txt/{urllib.parse.quote(alt)}")
        cands, fallas = [], []
        for url in intentos:
            try:
                j = http_met(url)
                guardar_debug("meteored_ubicacion.json", j)
                cands = buscar(j, lambda o: isinstance(o, dict) and isinstance(o.get("hash"), str))
                if cands:
                    break
                fallas.append(f"{urllib.parse.urlsplit(url).path}: sin resultados")
            except Exception as e:
                fallas.append(str(e))
        if not cands:
            raise RuntimeError("no encontré el lugar · " + " | ".join(fallas))
        def puntos(o):
            t = json.dumps(o, ensure_ascii=False).lower()
            return ("argentina" in t) * 2 + ("buenos aires" in t) + (CFG["lugar"].lower() in t)
        mejor = max(cands, key=puntos)
        estado["meteored_hash"] = mejor["hash"]
        estado["meteored_lugar"] = mejor.get("name") or mejor.get("description")
    j = http_met(f"{base}/forecast/v2/daily/{estado['meteored_hash']}")
    guardar_debug("meteored_diario.json", j)
    serie = extraer_diario(j)
    if not serie:
        raise RuntimeError("No pude leer el pronóstico de Meteored (ver datos/debug/meteored_diario.json)")
    return {"meteored": serie}

def fuente_accuweather(estado):
    """1 llamada por día (más 1 la primera vez)."""
    key = os.environ.get("ACCUWEATHER_KEY", "").strip()
    if not key:
        return None
    h = {"Authorization": "Bearer " + key}
    base = "https://dataservice.accuweather.com"
    if not estado.get("accu_key"):
        q = urllib.parse.urlencode({"apikey": key, "q": f"{CFG['lat']},{CFG['lon']}", "language": "es-ar"})
        j = http(f"{base}/locations/v1/cities/geoposition/search?{q}", h)
        estado["accu_key"] = j["Key"]
        estado["accu_lugar"] = j.get("LocalizedName")
    q = urllib.parse.urlencode({"apikey": key, "metric": "true", "details": "true", "language": "es-ar"})
    j = http(f"{base}/forecasts/v1/daily/5day/{estado['accu_key']}?{q}", h)
    serie = {}
    for d in j.get("DailyForecasts", []):
        lluvia = None
        for parte in ("Day", "Night"):
            v = num((d.get(parte) or {}).get("TotalLiquid"))
            if v is not None:
                lluvia = (lluvia or 0) + v
        probs = [num((d.get(p) or {}).get("PrecipitationProbability")) for p in ("Day", "Night")]
        probs = [p for p in probs if p is not None]
        serie[d["Date"][:10]] = {"tmax": num(d["Temperature"]["Maximum"]), "tmin": num(d["Temperature"]["Minimum"]),
                                 "lluvia": lluvia, "prob": max(probs) if probs else None}
    return {"accuweather": serie}


def guardar_debug(nombre, contenido):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    modo = "wb" if isinstance(contenido, bytes) else "w"
    with open(os.path.join(DEBUG_DIR, nombre), modo) as f:
        f.write(contenido if modo == "wb" else json.dumps(contenido, ensure_ascii=False, indent=1)[:200000])



MAX_METEORED_TOTAL = 45      # el plan gratis da 50 por día
LLAMADAS = {"meteored": 0}


def cargar_lugares():
    """Los lugares salen de lugares.json. Cada uno gasta 1 llamada diaria de Meteored."""
    ruta = os.path.join(RAIZ, "lugares.json")
    lugares = leer_json(ruta, [])
    return lugares or [{"nombre": "Lomas del Mirador", "buscar": "Lomas del Mirador", "lat": -34.66, "lon": -58.53}]


def recolectar_lugar(lugar, previo, hoy):
    CFG.update({"lugar": lugar.get("buscar") or lugar["nombre"], "lat": lugar["lat"], "lon": lugar["lon"], "cp": lugar.get("cp"), "provincia": lugar.get("provincia"), "alternativas": lugar.get("alternativas")})
    datos = dict(previo or {})
    datos.update({"nombre": lugar["nombre"], "lat": lugar["lat"], "lon": lugar["lon"]})
    hist = datos.setdefault("hist", {})
    estado = datos.setdefault("estado", {})
    if estado.get("meteored_buscado") != CFG["lugar"]:   # si cambió el lugar, volver a buscarlo
        estado.pop("meteored_hash", None)
        estado["meteored_buscado"] = CFG["lugar"]
    errores = {}
    hoy_s = hoy.isoformat()
    ya = hist.get(hoy_s, {})
    # El SMN está detrás de una verificación anti-robots (Cloudflare): no se consulta automáticamente.
    tareas = [("SMN", fuente_smn)] if os.environ.get("USAR_SMN") == "1" else []
    # Meteored: una consulta por corrida (3 por día), con tope diario para no pasar el límite de 50
    cont = estado.get("llamadas_meteored", {})
    if cont.get("fecha") != hoy_s:
        cont = {"fecha": hoy_s, "n": 0}
    limite = int(lugar.get("veces", 1))
    if cont["n"] < limite and LLAMADAS["meteored"] < MAX_METEORED_TOTAL:
        def _met():
            cont["n"] += 1
            return fuente_meteored(estado)
        tareas.append(("Meteored", _met))
    estado["llamadas_meteored"] = cont
    if "accuweather" not in ya:
        tareas.append(("AccuWeather", lambda: fuente_accuweather(estado)))
    for nombre, fn in tareas:
        try:
            r = fn()
            if r is None:
                continue
            if isinstance(r, tuple):
                r, datos["smn_estacion"] = r
            for k, serie in r.items():
                fut = {f: dia(v) for f, v in serie.items() if f >= hoy_s}
                if fut:
                    hist.setdefault(hoy_s, {})[k] = fut
        except Exception as e:
            errores[nombre] = f"{type(e).__name__}: {e}"[:250]
            traceback.print_exc()
    corte = (hoy - timedelta(days=40)).isoformat()
    datos["hist"] = {f: v for f, v in hist.items() if f >= corte}
    datos["errores"] = errores
    return datos


def main():
    hoy = datetime.now(TZ).date()
    viejo = leer_json(SALIDA, {})
    previos = {(round(l["lat"], 2), round(l["lon"], 2)): l for l in viejo.get("lugares", [])}
    if "hist" in viejo:   # formato anterior (un solo lugar)
        previos[(round(viejo.get("lat", 0), 2), round(viejo.get("lon", 0), 2))] = viejo
    hoy_s = hoy.isoformat()
    uso = viejo.get("uso_meteored", {})
    LLAMADAS["meteored"] = uso.get("n", 0) if uso.get("fecha") == hoy_s else 0
    salida = []
    for lugar in cargar_lugares():
        global DEBUG_DIR
        DEBUG_DIR = os.path.join(RAIZ, "datos", "debug", re.sub(r"[^a-z0-9]+", "-", lugar["nombre"].lower()).strip("-"))
        d = recolectar_lugar(lugar, previos.get((round(lugar["lat"], 2), round(lugar["lon"], 2))), hoy)
        salida.append(d)
        print(lugar["nombre"], json.dumps(d["errores"], ensure_ascii=False))
    guardar_json(SALIDA, {"lugares": salida, "actualizado": datetime.now(TZ).isoformat(timespec="minutes"),
                          "uso_meteored": {"fecha": hoy_s, "n": LLAMADAS["meteored"]}})


if __name__ == "__main__":
    main()
