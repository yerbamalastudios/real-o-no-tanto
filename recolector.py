#!/usr/bin/env python3
"""
Pronóstico real o no tanto · programa diario (GitHub Actions).

Baja una vez por día lo que pronostican el SMN, Meteored y AccuWeather (si hay clave)
y lo guarda en datos/sitios.json. La página lo usa para mostrar esos pronósticos y para
ver cuál acierta más. Los modelos globales y las efemérides los consulta la página sola.

Claves como secretos de GitHub: METEORED_KEY (y opcional ACCUWEATHER_KEY).
"""
import io, json, math, os, re, traceback, urllib.parse, urllib.request, zipfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

RAIZ = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(RAIZ, "datos", "sitios.json")
DEBUG_DIR = os.path.join(RAIZ, "datos", "debug")
CFG = {"lat": -34.66, "lon": -58.53, "zona_horaria": "America/Argentina/Buenos_Aires",
       "smn_estacion_pronostico": ["MORON", "PALOMAR", "AEROPARQUE", "BUENOS_AIRES", "EZEIZA"]}
TZ = ZoneInfo(CFG["zona_horaria"])
UA = {"User-Agent": "pronostico-real-o-no-tanto/1.0 (uso personal)"}


MESES = {"ENE": 1, "JAN": 1, "FEB": 2, "MAR": 3, "ABR": 4, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7,
         "AGO": 8, "AUG": 8, "SEP": 9, "SET": 9, "OCT": 10, "NOV": 11, "DIC": 12, "DEC": 12}


LINEA_PRON = re.compile(
    r"(\d{1,2})/([A-Za-z]{3})/(\d{4})\s+(\d{1,2})\s*Hs\.?\s+(-?\d+(?:[.,]\d+)?)\s+\d+\s*\|\s*\d+\s+(\d+(?:[.,]\d+)?)")


def http(url, headers=None, binario=False, timeout=40):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        cuerpo = r.read()
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


def fuente_smn():
    texto = smn_zip("pron5d")
    est = parse_pron5d(texto)
    if not est:
        guardar_debug("smn_pron5d.txt", texto)
        raise RuntimeError("No pude leer el archivo pron5d del SMN (guardado en debug/)")
    nombre = elegir(est, CFG["smn_estacion_pronostico"])
    if not nombre:
        raise RuntimeError("Ninguna estación preferida en pron5d. Disponibles: " + ", ".join(sorted(est)[:40]))
    porDia = {}
    for f, hh, t, p in est[nombre]:
        porDia.setdefault(f.isoformat(), []).append((t, p))
    serie = {}
    for f, v in porDia.items():
        if len(v) < 6:  # día incompleto (menos de 6 de los 8 horarios)
            continue
        ts = [x[0] for x in v]
        serie[f] = {"tmax": max(ts), "tmin": min(ts), "lluvia": round(sum(x[1] for x in v), 1), "prob": None}
    return {"smn": serie}, nombre


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
    h = {"x-api-key": key, "Accept": "application/json"}
    base = "https://api.meteored.com/api"
    if not estado.get("meteored_hash"):
        j = http(f"{base}/location/v1/search/coords/{CFG['lat']}/{CFG['lon']}", h)
        guardar_debug("meteored_ubicacion.json", j)
        hashes = buscar(j, lambda o: isinstance(o, dict) and isinstance(o.get("hash"), str))
        if not hashes:
            raise RuntimeError("Meteored no devolvió una ubicación (ver debug/meteored_ubicacion.json)")
        estado["meteored_hash"] = hashes[0]["hash"]
        estado["meteored_lugar"] = hashes[0].get("name") or hashes[0].get("description")
    j = http(f"{base}/forecast/v1/daily/{estado['meteored_hash']}", h)
    guardar_debug("meteored_diario.json", j)
    serie = extraer_diario(j)
    if not serie:
        raise RuntimeError("No pude leer el pronóstico de Meteored (ver debug/meteored_diario.json)")
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


def main():
    hoy = datetime.now(TZ).date()
    datos = leer_json(SALIDA, {})
    datos.update({"lat": CFG["lat"], "lon": CFG["lon"]})
    hist = datos.setdefault("hist", {})
    estado = datos.setdefault("estado", {})
    errores = {}
    hoy_s = hoy.isoformat()
    ya = hist.get(hoy_s, {})
    tareas = [("SMN", lambda: fuente_smn())]
    if "meteored" not in ya:   # 1 sola llamada por día aunque el programa corra dos veces
        tareas.append(("Meteored", lambda: fuente_meteored(estado)))
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
    datos["actualizado"] = datetime.now(TZ).isoformat(timespec="minutes")
    guardar_json(SALIDA, datos)
    print("Listo.", json.dumps(errores, ensure_ascii=False))


if __name__ == "__main__":
    main()
