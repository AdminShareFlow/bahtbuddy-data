#!/usr/bin/env python3
"""Robot de mise à jour des données de Baht Buddy (lancé chaque semaine par GitHub Actions).

1. Télécharge distributeurs, banques et bureaux de change de Thaïlande depuis OpenStreetMap
   (Overpass, carré par carré). Un carré qui échoue reprend les données de la semaine d'avant.
2. Ajoute les lieux d'Overture Maps absents d'OpenStreetMap (doublon = même type à < 60 m).
3. Écrit places-th.json (lu par l'appli) et meta.json (petit fichier que l'appli consulte
   pour savoir s'il y a du nouveau).

Garde-fou : si le résultat contient beaucoup moins de lieux que la version précédente,
rien n'est publié (mieux vaut des données d'une semaine que des données cassées).
"""
import datetime, hashlib, json, math, os, re, sys, time, urllib.parse, urllib.request
from shapely.geometry import shape, Point, box
from shapely.prepared import prep

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BORDER = shape(json.load(open(f"{HERE}/th_border.geojson")))
INSIDE = prep(BORDER)
BANKS = json.load(open(f"{ROOT}/banks.json"))["banks"]
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
if os.environ.get("OVERPASS_URLS"):  # pour les tests
    OVERPASS = os.environ["OVERPASS_URLS"].split(",")
UA = {"User-Agent": "BahtBuddy-data-bot/1.0 (+https://github.com/AdminShareFlow/bahtbuddy-data)"}
CACHE = os.environ.get("TILE_CACHE", "/tmp/bahtbuddy-tiles")
MIN_RATIO = float(os.environ.get("MIN_RATIO", "0.7"))  # en dessous de 70 % du nombre de lieux précédent, on ne publie pas


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------- Reconnaissance des banques ----------
_M = []
for b in BANKS:
    for k in b["keys"]:
        if re.fullmatch(r"[a-z ]+", k):
            rx = re.compile(r"(^|[^a-z])" + k.replace(" ", r"\s*") + r"([^a-z]|$)")
            _M.append((b["id"], lambda s, rx=rx: bool(rx.search(s))))
        else:
            _M.append((b["id"], lambda s, k=k: k in s))


def bank_of(text):
    s = (text or "").lower()
    for bid, test in _M:
        if s and test(s):
            return bid
    return None


def osm_item(el):
    lat = el.get("lat", el.get("center", {}).get("lat"))
    lon = el.get("lon", el.get("center", {}).get("lon"))
    if lat is None or not INSIDE.contains(Point(lon, lat)):
        return None
    tags = el.get("tags", {})
    t = {"atm": "a", "bank": "b"}.get(tags.get("amenity"), "x")
    label = " | ".join(tags[k] for k in ["brand", "brand:en", "operator", "operator:en", "name", "name:en", "name:th", "network"] if tags.get(k))
    name = (tags.get("name:en") or tags.get("name") or tags.get("brand") or tags.get("operator") or "")[:60]
    return [el["type"][0] + str(el["id"]), round(lat, 5), round(lon, 5), t, None if t == "x" else bank_of(label),
            name, (tags.get("opening_hours") or "")[:40], 1 if t == "b" and tags.get("atm") == "yes" else 0]


# ---------- OpenStreetMap ----------
def overpass(q):
    body = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for url in OVERPASS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=body, headers=UA)
                with urllib.request.urlopen(req, timeout=180) as r:
                    j = json.load(r)
                if "elements" not in j:
                    raise RuntimeError("réponse sans données")
                if j.get("remark") and "error" in j["remark"].lower():
                    raise RuntimeError(j["remark"][:120])
                return j["elements"]
            except Exception as e:
                last = e
                log(f"    échec {url.split('/')[2]} : {e}")
                if "429" in str(e):
                    time.sleep(30)
    raise RuntimeError(str(last))


def tile(s, w, size, failed, depth=0):
    """Un carré ; s'il échoue il est redécoupé en 4 (3 niveaux), puis noté comme raté."""
    if not BORDER.intersects(box(w, s, w + size, s + size)):
        return []
    f = f"{CACHE}/{s:.4f}_{w:.4f}_{size:.4f}.json"
    if os.path.exists(f):
        return json.load(open(f))
    bb = f"{s},{w},{s + size},{w + size}"
    q = (f'[out:json][timeout:120];(nwr["amenity"="atm"]({bb});nwr["amenity"="bank"]({bb});'
         f'nwr["amenity"="bureau_de_change"]({bb}););out center tags;')
    try:
        els = overpass(q)
        time.sleep(1)  # politesse envers le serveur public
    except Exception:
        if depth >= 3:
            failed.append((w, s, w + size, s + size))
            return []
        h = size / 2
        els = []
        for ds in (0, h):
            for dw in (0, h):
                els += tile(s + ds, w + dw, h, failed, depth + 1)
        if not any(fb for fb in failed if fb[0] >= w and fb[1] >= s and fb[2] <= w + size and fb[3] <= s + size):
            json.dump(els, open(f, "w"))
        return els
    json.dump(els, open(f, "w"))
    log(f"  {bb} : {len(els)} éléments")
    return els


def fetch_osm(previous):
    os.makedirs(CACHE, exist_ok=True)
    failed, seen, items = [], set(), []
    only = os.environ.get("ONLY_TILE")  # pour les tests : "lat,lon"
    cells = [tuple(map(int, only.split(",")))] if only else [(la, lo) for la in range(5, 21) for lo in range(97, 106)]
    for la, lo in cells:
        for el in tile(float(la), float(lo), 1.0, failed):
            it = osm_item(el)
            if it and it[0] not in seen:
                seen.add(it[0]); items.append(it)
    # Carrés ratés : on garde les lieux OSM de la semaine précédente dans ces zones
    kept = 0
    for w, s, e, n in failed:
        for it in previous:
            if it[0][0] in "nwr" and it[0] not in seen and s <= it[1] < n and w <= it[2] < e:
                seen.add(it[0]); items.append(it); kept += 1
    log(f"OSM : {len(items)} lieux, {len(failed)} zone(s) ratée(s), {kept} lieux repris de la version précédente")
    return items, len(failed)


# ---------- Overture Maps ----------
def latest_overture_release():
    url = "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/?list-type=2&prefix=release/&delimiter=/"
    xml = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode()
    return sorted(re.findall(r"<Prefix>release/([^<]+)/</Prefix>", xml))[-1]


def fetch_overture():
    import duckdb
    con = duckdb.connect()
    ext = os.environ.get("DUCKDB_EXT_DIR")
    con.execute(f"INSTALL '{ext}/httpfs.duckdb_extension'" if ext else "INSTALL httpfs")
    con.execute("LOAD httpfs")
    if os.environ.get("HTTPS_PROXY"):  # uniquement pour les tests derrière un proxy
        con.execute(f"SET http_proxy='{os.environ['HTTPS_PROXY'].replace('http://', '')}'")
        if os.environ.get("SSL_CERT_FILE"):
            con.execute(f"SET ca_cert_file='{os.environ['SSL_CERT_FILE']}'")
    con.execute("CREATE SECRET ov (TYPE S3, PROVIDER config, KEY_ID '', SECRET '', REGION 'us-west-2')")
    rel = latest_overture_release()
    rows = con.execute(f"""
      SELECT id, bbox.ymin, bbox.xmin, names."primary", brand.names."primary", taxonomy."primary"
      FROM read_parquet('s3://overturemaps-us-west-2/release/{rel}/theme=places/type=place/*')
      WHERE bbox.xmin BETWEEN 97.3 AND 105.7 AND bbox.ymin BETWEEN 5.5 AND 20.5
        AND taxonomy."primary" IN ('atm', 'bank', 'bank_or_credit_union', 'currency_exchange')
        AND coalesce(operating_status, 'open') = 'open' AND coalesce(confidence, 1) >= 0.5""").fetchall()
    out = []
    for oid, lat, lon, name, brand, cat in rows:
        if not INSIDE.contains(Point(lon, lat)):
            continue
        t = {"atm": "a", "currency_exchange": "x"}.get(cat, "b")
        label = " ".join(x for x in [brand, name] if x)
        out.append(["o" + oid[:12], round(lat, 5), round(lon, 5), t, None if t == "x" else bank_of(label),
                    (name or brand or "")[:60], "", 0])
    log(f"Overture {rel} : {len(out)} lieux")
    return out, rel


def merge(osm, extra):
    grid = {}
    key = lambda la, lo: (int(la * 1000), int(lo * 1000))
    for it in osm:
        grid.setdefault(key(it[1], it[2]), []).append(it)

    def near(t, la, lo):
        k = key(la, lo)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for it in grid.get((k[0] + dy, k[1] + dx), []):
                    if it[3] == t and math.hypot((it[1] - la) * 111000, (it[2] - lo) * 111000 * math.cos(math.radians(la))) < 60:
                        return True
        return False

    items = list(osm)
    for it in extra:
        if not near(it[3], it[1], it[2]):
            items.append(it)
            grid.setdefault(key(it[1], it[2]), []).append(it)
    return items


def main():
    prev_path = f"{ROOT}/places-th.json"
    previous = json.load(open(prev_path)) if os.path.exists(prev_path) else {"items": [], "overture": None}
    prev_items = previous["items"]

    try:
        osm, osm_failed = fetch_osm(prev_items)
    except Exception as e:
        log("OSM indisponible, on garde la version précédente :", e)
        osm, osm_failed = [it for it in prev_items if it[0][0] in "nwr"], -1
    try:
        ov, rel = fetch_overture()
    except Exception as e:
        log("Overture indisponible, on garde la version précédente :", e)
        ov, rel = [it for it in prev_items if it[0][0] == "o"], previous.get("overture")

    items = merge(osm, ov)
    if prev_items and len(items) < MIN_RATIO * len(prev_items):
        raise SystemExit(f"Garde-fou : {len(items)} lieux contre {len(prev_items)} avant. Rien n'est publié.")

    today = datetime.date.today().isoformat()
    counts = {t: sum(1 for it in items if it[3] == t) for t in "abx"}
    data = {"date": today, "sources": "OpenStreetMap (ODbL) + Overture Maps (CDLA-Permissive-2.0)",
            "overture": rel, "osm_failed_zones": osm_failed, "items": items}
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    open(prev_path, "wb").write(raw)
    banks = json.load(open(f"{ROOT}/banks.json"))
    meta = {"generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "places": {"date": today, "count": len(items), "atm": counts["a"], "bank": counts["b"], "fx": counts["x"],
                       "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
            "banks": {"checked": banks["checked"]}}
    json.dump(meta, open(f"{ROOT}/meta.json", "w"), indent=1)
    log(f"Publié : {len(items)} lieux ({counts['a']} ATM, {counts['b']} banques, {counts['x']} change)")


if __name__ == "__main__":
    main()
