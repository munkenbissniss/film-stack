"""Setter opp og kobler sammen alle appene automatisk via API-ene deres.

Kjøres av «docker compose up -d» (tjenesten autoconfig). Trygt å kjøre igjen
når som helst – den sjekker hva som allerede er gjort og retter bare avvik:
    docker compose run --rm autoconfig
"""
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
import quality  # noqa: E402
from common import _has_content  # noqa: E402
from common import (CFG, Session, docker_action, docker_exec,  # noqa: E402
                    docker_logs, env, http, ini_get, ini_set, log, media_dirs, ok,
                    plex_pref, read, step, wait_container, wait_file, wait_http, warn,
                    write, xml_apikey)

PUID, PGID = int(env("PUID", "1000")), int(env("PGID", "1000"))
USER, PASSWORD = env("ADMIN_USER", "admin"), env("ADMIN_PASSWORD")
MEDIA = env("MEDIA_SERVER", "jellyfin").lower()         # jellyfin | plex
JF = env("JELLYFIN_URL", "http://jellyfin:8096").rstrip("/")
JF_HOST = urllib.parse.urlparse(JF).hostname
ACCOUNT_TOKEN = env("PLEX_ACCOUNT_TOKEN")
PLEX_URL = env("PLEX_INTERNAL_URL", "http://plex:32400").rstrip("/")
PLEX_HOST = urllib.parse.urlparse(PLEX_URL).hostname
HOST_MODE = env("PLEX_HOST_MODE") == "1"
QBIT = f"http://{env('QBIT_HOST', 'gluetun')}:8080"
LAN_IP, DASH = env("LAN_IP"), env("DASHBOARD_HOST", env("LAN_IP", "localhost"))
PROFILES = [p for p in env("COMPOSE_PROFILES").split(",") if p]
MOVIES, TV = media_dirs()
USENET = "usenet" in PROFILES
WHISPER = env("WHISPER", "av").lower()                   # av | cpu | gpu | ekstern
WHISPER_URL = env("WHISPER_URL", "http://whisper:9000").rstrip("/")
WHISPER_AUTO = env("WHISPER_AUTO", "0") == "1"

STATE = {}          # nøkler og ID-er vi finner underveis
RESULTS = []        # (app, status, melding) til oppsummeringen
TODO = []           # det som må gjøres for hånd


def result(app, good, msg):
    RESULTS.append((app, good, msg))
    (ok if good else warn)(f"{app}: {msg}")


def run(app, fn):
    step(app)
    try:
        fn()
    except Exception as e:  # én app som feiler skal ikke stoppe resten
        result(app, False, f"uventet feil: {e}")


# =========================================================================== *arr
class Arr:
    def __init__(self, name, url, key, ver="v3"):
        self.name, self.base, self.key, self.ver = name, url.rstrip("/"), key, ver

    def call(self, method, path, body=None):
        r = http(method, f"{self.base}/api/{self.ver}/{path}", json_body=body,
                 headers={"X-Api-Key": self.key, "Accept": "application/json"}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"{method} {path} → {r.status} {r.text[:200]}")
        return r.json()

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body):
        return self.call("POST", path, body)

    def put(self, path, body):
        return self.call("PUT", path, body)

    def put_config(self, name, obj):
        try:
            return self.put(f"config/{name}/{obj.get('id', 1)}", obj)
        except RuntimeError:
            return self.put(f"config/{name}", obj)

    def set_auth(self):
        if not PASSWORD:
            return
        h = self.get("config/host")
        h.update(authenticationMethod="forms", authenticationRequired="enabled",
                 username=USER, password=PASSWORD, passwordConfirmation=PASSWORD)
        self.put_config("host", h)

    def upsert(self, kind, implementation, name, values, extra=None, match=None):
        """Oppretter eller oppdaterer en nedlastingsklient/tilkobling/app."""
        items = [i for i in self.get(kind) if i.get("implementation") == implementation
                 and (match is None or match(i))]
        if items:
            obj, verb = items[0], "oppdatert"
        else:
            obj = next(s for s in self.get(f"{kind}/schema") if s.get("implementation") == implementation)
            obj.update(name=name, enable=True)
            verb = "lagt til"
        for f in obj.get("fields", []):
            if f.get("name") in values:
                f["value"] = values[f["name"]]
        for k, v in (extra or {}).items():   # bare felt appen faktisk kjenner
            if k in obj:
                obj[k] = v
        if "id" in obj and items:
            self.put(f"{kind}/{obj['id']}?forceSave=true", obj)
        else:
            self.post(f"{kind}?forceSave=true", obj)
        return verb


def arr_key(app):
    path = f"{CFG}/{app}/config.xml"
    if not wait_file(path, 180):
        return ""
    for _ in range(20):                 # fila lages før nøkkelen skrives
        k = xml_apikey(path)
        if k:
            return k
        time.sleep(3)
    return ""


def setup_arr(app, port, kind):
    key = arr_key(app)
    if not key:
        return result(app, False, "fant ikke API-nøkkel (har containeren startet?)")
    STATE[f"{app}_key"] = key
    a = Arr(app, f"http://{app}:{port}", key)
    if not wait_http(f"http://{app}:{port}/ping", 180, app):
        return result(app, False, "svarer ikke")
    for _ in range(20):                 # API-et kan bruke litt tid etter /ping
        try:
            a.get("system/status")
            break
        except RuntimeError:
            time.sleep(3)

    a.set_auth()
    ok("innlogging satt")

    path = f"/data/{MOVIES if kind == 'movie' else TV}"
    roots = a.get("rootfolder")
    if not any(r["path"].rstrip("/") == path for r in roots):
        a.post("rootfolder", {"path": path})
        ok(f"rotmappe {path} lagt til")
    else:
        ok(f"rotmappe {path} finnes")
    media = a.get("movie" if kind == "movie" else "series")
    for r in roots:                      # rydd bort gamle rotmapper under /data som ingen bruker
        rp = r["path"].rstrip("/")
        if rp == path or not rp.startswith("/data/"):
            continue
        in_use = any((m.get("path") or "").startswith(rp + "/") for m in media)
        if not in_use and not _has_content(rp):   # bare tomme/manglende mapper fjernes
            try:
                a.call("DELETE", f"rootfolder/{r['id']}")
                ok(f"fjernet ubrukt rotmappe {rp}")
            except RuntimeError:
                pass

    try:
        dc = a.get("config/downloadclient")
        if isinstance(dc, dict):
            if not dc.get("autoRedownloadFailed", True):
                dc["autoRedownloadFailed"] = True
                a.put_config("downloadclient", dc)
            ok("prøver neste utgivelse automatisk hvis en nedlasting feiler")
    except (RuntimeError, ValueError):
        pass

    mm = a.get("config/mediamanagement")
    if not mm.get("copyUsingHardlinks", True):
        mm["copyUsingHardlinks"] = True
        a.put_config("mediamanagement", mm)
    ok("hardlinks på")

    cat_field = "movieCategory" if kind == "movie" else "tvCategory"
    category = "movies" if kind == "movie" else "tv"
    clients = [("qBittorrent", "QBittorrent", STATE.get("qbit_ok"),
                {"host": urllib.parse.urlparse(QBIT).hostname, "port": 8080}, "qBittorrent")]
    if USENET:
        clients.append(("NZBGet", "Nzbget", STATE.get("nzbget_ok"), {"host": "nzbget", "port": 6789}, "NZBGet"))
    missing = []
    for label, impl, ready, conn, name in clients:
        if not ready:
            missing.append(label)
            warn(f"{label} hoppet over – ikke klar (se {label} over)")
            continue
        try:
            v = a.upsert("downloadclient", impl, name,
                         {**conn, "useSsl": False, "username": USER, "password": PASSWORD,
                          cat_field: category}, {"priority": 1})
            ok(f"{label} {v}")
        except RuntimeError as e:
            missing.append(label)
            warn(f"{label}: {str(e)[:160]}")
    if missing:
        TODO.append(f"{' og '.join(missing)} var ikke klar(e). Når de kjører: «docker compose run --rm autoconfig».")

    if MEDIA == "plex" and STATE.get("plex_token"):
        setup_plex_notification(a, kind)
    elif MEDIA == "jellyfin" and STATE.get("jf_key"):
        setup_jellyfin_notification(a)
    result(app, True, "klar" if not missing else f"klar, men mangler {' og '.join(missing)}")


PLEX_TRIGGERS = ["onDownload", "onUpgrade", "onRename", "onImportComplete", "onSeriesDelete",
                 "onEpisodeFileDelete", "onEpisodeFileDeleteForUpgrade", "onMovieDelete",
                 "onMovieFileDelete", "onMovieFileDeleteForUpgrade"]


def setup_plex_notification(a, kind):
    """Sonarr/Radarr ber Plex oppdatere biblioteket når en fil er importert."""
    try:
        v = a.upsert("notification", "PlexServer", "Plex",
                     {"host": PLEX_HOST, "port": 32400, "useSsl": False,
                      "authToken": STATE["plex_token"], "updateLibrary": True},
                     {k: True for k in PLEX_TRIGGERS} | {"tags": []})
        ok(f"Plex-varsling {v} (oppdaterer biblioteket ved nye filer)")
    except RuntimeError as e:
        if "library is required" in str(e):
            lib = "Filmer" if kind == "movie" else "Serier"
            warn(f"Plex-varsling: Plex mangler et {lib}-bibliotek ennå")
            TODO.append(f"Plex: lag biblioteket {lib} og kjør «docker compose run --rm autoconfig» igjen.")
        else:
            warn(f"Plex-varsling: {str(e)[:160]}")


def setup_jellyfin_notification(a):
    """Sonarr/Radarr ber Jellyfin oppdatere biblioteket når en fil er importert."""
    for n in a.get("notification"):               # byttet fra Plex: fjern den gamle varslingen
        if n.get("implementation") == "PlexServer":
            try:
                a.call("DELETE", f"notification/{n['id']}")
                ok("gammel Plex-varsling fjernet")
            except RuntimeError:
                pass
    try:
        v = a.upsert("notification", "MediaBrowser", "Jellyfin",
                     {"host": JF_HOST, "port": urllib.parse.urlparse(JF).port or 8096, "useSsl": False,
                      "apiKey": STATE["jf_key"], "notify": False, "updateLibrary": True},
                     {k: True for k in PLEX_TRIGGERS} | {"tags": []})
        ok(f"Jellyfin-varsling {v} (oppdaterer biblioteket ved nye filer)")
    except RuntimeError as e:
        warn(f"Jellyfin-varsling: {str(e)[:160]}")


def wait_ready(url, timeout=600, name=None):
    """Venter til en tjeneste svarer 200 (ikke bare at den svarer)."""
    end = time.time() + timeout
    while time.time() < end:
        r = http("GET", url, headers={"Accept": "application/json"}, timeout=10)
        if r.status == 200:
            return True
        time.sleep(5)
    warn(f"{name or url} ble ikke klar innen {timeout} sekunder")
    return False


# =========================================================================== Jellyfin
JF_AUTH = 'MediaBrowser Client="film-stack", Device="autoconfig", DeviceId="film-stack-autoconfig", Version="1.0"'


def jf(method, path, token=None, body=None, timeout=60):
    h = {"Authorization": JF_AUTH + (f', Token="{token}"' if token else ""), "Accept": "application/json"}
    return http(method, JF + path, headers=h, json_body=body, timeout=timeout)


def setup_jellyfin():
    if not wait_ready(f"{JF}/System/Info/Public", 600, "Jellyfin"):
        return result("Jellyfin", False, "ble ikke klar")
    info = jf("GET", "/System/Info/Public").json() or {}
    lang, country = env("METADATA_LANGUAGE", "no"), env("METADATA_COUNTRY", "NO")
    if not info.get("StartupWizardCompleted"):
        if not PASSWORD:
            TODO.append(f"Jellyfin: åpne http://{DASH}:8096 og fullfør oppstartsveiviseren.")
            return result("Jellyfin", False, "mangler ADMIN_PASSWORD i .env")
        jf("POST", "/Startup/Configuration", body={"UICulture": "nb" if lang == "no" else "en-US",
                                                    "MetadataCountryCode": country, "PreferredMetadataLanguage": lang})
        jf("GET", "/Startup/User")                      # lager førstebrukeren
        r = jf("POST", "/Startup/User", body={"Name": USER, "Password": PASSWORD})
        if not r.ok:
            return result("Jellyfin", False, f"kunne ikke lage administrator ({r.status} {r.text[:120]})")
        jf("POST", "/Startup/RemoteAccess", body={"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False})
        r = jf("POST", "/Startup/Complete")
        if not r.ok:
            return result("Jellyfin", False, f"oppstartsveiviseren ble ikke fullført ({r.status})")
        ok(f"administrator {USER} laget, språk {lang}, land {country}")
        for _ in range(20):                              # Jellyfin trenger et øyeblikk etterpå
            if (jf("GET", "/System/Info/Public").json() or {}).get("StartupWizardCompleted"):
                break
            time.sleep(3)

    r = jf("POST", "/Users/AuthenticateByName", body={"Username": USER, "Pw": PASSWORD})
    token = (r.json() or {}).get("AccessToken") if r.ok else None
    if not token:
        TODO.append("Jellyfin: kom ikke inn med brukernavn/passord fra .env. Er passordet endret i Jellyfin? "
                    "Sett det tilbake, eller rett ADMIN_PASSWORD og kjør autoconfig igjen.")
        return result("Jellyfin", False, f"innlogging feilet ({r.status})")

    def find_key():
        items = (jf("GET", "/Auth/Keys", token).json() or {}).get("Items") or []
        return next((k.get("AccessToken") for k in items if k.get("AppName") == "film-stack"), "")
    key = find_key()
    if not key:
        jf("POST", "/Auth/Keys?app=film-stack", token)
        key = find_key()
    if not key:
        return result("Jellyfin", False, "fikk ikke laget API-nøkkel")
    STATE["jf_key"] = key
    ok("API-nøkkel for Sonarr, Radarr, Seerr og dashboardet")

    if env("HWACCEL") == "1":
        enc = jf("GET", "/System/Configuration/encoding", key).json()
        if isinstance(enc, dict) and enc.get("HardwareAccelerationType") in (None, "", "none"):
            enc.update(HardwareAccelerationType="vaapi", VaapiDevice="/dev/dri/renderD128",
                       EnableHardwareEncoding=True, HardwareDecodingCodecs=["h264", "mpeg2video", "vc1"])
            if jf("POST", "/System/Configuration/encoding", key, enc).ok:
                ok("maskinvare-transkoding (VAAPI) på")

    folders = jf("GET", "/Library/VirtualFolders", key).json() or []
    paths = {loc.rstrip("/") for f in folders if isinstance(f, dict) for loc in f.get("Locations") or []}
    for name, ctype, path in (("Filmer", "movies", f"/data/{MOVIES}"), ("Serier", "tvshows", f"/data/{TV}")):
        if path in paths:
            ok(f"bibliotek for {path} finnes")
            continue
        q = urllib.parse.urlencode({"name": name, "collectionType": ctype, "refreshLibrary": "true", "paths": path})
        r = jf("POST", f"/Library/VirtualFolders?{q}", key, {"LibraryOptions": {
            "PathInfos": [{"Path": path}], "EnableRealtimeMonitor": True,
            "PreferredMetadataLanguage": lang, "MetadataCountryCode": country}}, timeout=120)
        ok(f"bibliotek «{name}» laget ({path})") if r.ok else warn(f"klarte ikke lage «{name}» ({r.status} {r.text[:120]})")

    STATE["jf_name"] = info.get("ServerName") or "Jellyfin"
    users = jf("GET", "/Users", key).json()
    if isinstance(users, list) and len(users) <= 1:
        TODO.append("Jellyfin: lag en bruker til hver i familien (Kontrollpanel → Brukere), eller inviter dem med Wizarr. "
                    "Apper: Jellyfin (Android, Android TV, iPhone), Swiftfin (iPhone, Apple TV), Findroid.")
    result("Jellyfin", True, f"klar – http://{DASH}:8096")


# =========================================================================== Plex
def plex_headers(token=None):
    h = {"Accept": "application/json", "X-Plex-Client-Identifier": "film-stack-autoconfig",
         "X-Plex-Product": "Plex Stack"}
    if token:
        h["X-Plex-Token"] = token
    return h


def setup_plex():
    if not wait_ready(f"{PLEX_URL}/identity", 600, "Plex"):
        return result("Plex", False, "ble ikke klar (oppdaterer kanskje databasen – kjør autoconfig igjen om litt)")
    token = plex_pref("PlexOnlineToken")
    ident = http("GET", f"{PLEX_URL}/identity", headers={"Accept": "application/json"}).json() or {}
    if not token and str((ident.get("MediaContainer") or {}).get("claimed", "")) in ("1", "true", "True"):
        log("  Plex er allerede knyttet til en konto – venter på at tokenet skrives …")
        for _ in range(30):                 # Plex skriver Preferences.xml litt etter oppstart
            time.sleep(2)
            token = plex_pref("PlexOnlineToken")
            if token:
                break

    if not token and ACCOUNT_TOKEN:
        r = http("GET", "https://plex.tv/api/claim/token.json", headers=plex_headers(ACCOUNT_TOKEN))
        claim = (r.json() or {}).get("token") if r.ok else None
        if claim:
            out = ""
            for attempt in range(20):           # Plex kan svare 503 mens databasen oppdateres
                code, out = docker_exec("plex", ["curl", "-s", "-X", "POST",
                                                 f"http://127.0.0.1:32400/myplex/claim?token={claim}"])
                if 'code="503"' not in (out or ""):
                    break
                log("  Plex oppdaterer databasen – venter …")
                time.sleep(15)
            for _ in range(30):
                time.sleep(2)
                token = plex_pref("PlexOnlineToken")
                if token:
                    break
            ok("serveren er knyttet til Plex-kontoen din") if token else warn(f"claim feilet: {out[:200]}")
        else:
            warn(f"fikk ikke claim-kode fra plex.tv ({r.status})")

    if not token:
        TODO.append("Plex: åpne http://<server>:32400/web og knytt serveren til kontoen din, "
                    "eller kjør veiviseren igjen og logg inn i Plex.")
        return result("Plex", False, "ikke knyttet til en Plex-konto ennå")
    def admin_token(seconds):
        """Et token som kan ENDRE innstillinger (/:/prefs er bare for eieren)."""
        end = time.time() + seconds
        while True:
            for tok in dict.fromkeys(t for t in (plex_pref("PlexOnlineToken") or token, ACCOUNT_TOKEN) if t):
                if http("GET", f"{PLEX_URL}/:/prefs", headers=plex_headers(tok)).status == 200:
                    return tok
            if time.time() > end:
                return None
            time.sleep(5)

    log("  venter på at Plex gir administratortilgang …")
    good = admin_token(300)                  # rett etter claim avviser Plex endringer (403) en stund
    if not good:
        log("  Plex gir ikke tilgang ennå – starter Plex på nytt …")
        docker_action("plex", "restart")
        wait_ready(f"{PLEX_URL}/identity", 300, "Plex")
        good = admin_token(180)
    if not good:
        TODO.append("Plex: kjør «docker compose run --rm autoconfig» igjen om noen minutter.")
        return result("Plex", False, "gir ikke administratortilgang ennå (403)")
    token = good
    STATE["plex_token"] = token

    prefs = {"TranscoderTempDirectory": "/transcode"}
    if env("HWACCEL") == "1":
        prefs.update(HardwareAcceleratedCodecs="1", HardwareAcceleratedEncoders="1")
    if not HOST_MODE and LAN_IP:   # Docker Desktop: fortell Plex hvor den faktisk kan nås
        prefs["customConnections"] = f"http://{LAN_IP}:32400"
        prefs["LanNetworks"] = "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12"
    for k, v in prefs.items():
        r = http("PUT", f"{PLEX_URL}/:/prefs?{k}={urllib.parse.quote(v)}", headers=plex_headers(token))
        if not r.ok:
            warn(f"innstilling {k} ble ikke satt ({r.status})")
    ok("innstillinger satt (transkodingsmappe"
       + (", maskinvare-transkoding" if env("HWACCEL") == "1" else "") + ")")

    r = http("GET", f"{PLEX_URL}/library/sections", headers=plex_headers(token))
    sections = ((r.json() or {}).get("MediaContainer", {}) or {}).get("Directory", []) if r.ok else []
    paths = {loc.get("path", "").rstrip("/") for s in sections for loc in s.get("Location", [])}
    lang = env("LIBRARY_LANGUAGE", "en-US")
    for name, typ, agent, scanner, path in (
            ("Filmer", "movie", "tv.plex.agents.movie", "Plex Movie", f"/data/{MOVIES}"),
            ("Serier", "show", "tv.plex.agents.series", "Plex TV Series", f"/data/{TV}")):
        if path in paths:
            ok(f"bibliotek for {path} finnes")
            continue
        q = urllib.parse.urlencode({"name": name, "type": typ, "agent": agent, "scanner": scanner,
                                    "language": lang, "location": path})
        r = http("POST", f"{PLEX_URL}/library/sections?{q}", headers=plex_headers(token))
        ok(f"bibliotek «{name}» laget ({path})") if r.ok else warn(f"klarte ikke lage «{name}» ({r.status})")

    r = http("GET", f"{PLEX_URL}/", headers=plex_headers(token))
    mc = (r.json() or {}).get("MediaContainer", {}) if r.ok else {}
    STATE["plex_id"], STATE["plex_name"] = mc.get("machineIdentifier", ""), mc.get("friendlyName", "Plex")
    result("Plex", True, f"klar ({STATE['plex_name']})")


# =========================================================================== qBittorrent
def setup_qbit():
    s = Session()
    h = {"Referer": QBIT, "Origin": QBIT}
    if not wait_http(f"{QBIT}/api/v2/app/version", 240, "qBittorrent"):
        TODO.append("qBittorrent/VPN: sjekk «docker logs gluetun» – VPN-en må være oppe for nedlasting.")
        return result("qBittorrent", False, "svarer ikke (er VPN-en oppe?)")

    def logged_in():
        return s.http("GET", f"{QBIT}/api/v2/app/version", headers=h).status == 200

    def login(u, p):
        r = s.http("POST", f"{QBIT}/api/v2/auth/login", headers=h, form={"username": u, "password": p})
        if not (r.status in (200, 204) and "fail" not in r.text.lower()):   # qBittorrent 5 svarer 204
            log(f"  innlogging som {u}: {r.status} {r.text.strip()[:80]}")
            return False
        return True

    if not logged_in():
        temp = re.findall(r"temporary password[^:]*:\s*(\S+)", docker_logs("qbittorrent"), re.I)
        if not (PASSWORD and login(USER, PASSWORD)) and not (temp and login("admin", temp[-1])):
            log("  kommer ikke inn med passord – skriver innlogging direkte i qBittorrent sin konfig")
            if not qbit_write_config():
                return result("qBittorrent", False, "fant ikke konfigfila til qBittorrent")
            if not wait_http(f"{QBIT}/api/v2/app/version", 120, "qBittorrent"):
                return result("qBittorrent", False, "startet ikke igjen")
            for _ in range(20):
                if logged_in():
                    break
                time.sleep(3)
            else:
                TODO.append("qBittorrent: kom ikke inn – send «docker logs qbittorrent --tail 30» til den som hjelper deg.")
                return result("qBittorrent", False, "kom ikke inn selv etter at konfigfila ble skrevet")
            ok("innlogging satt i konfigfila")

    prefs = {"save_path": "/data/downloads", "temp_path_enabled": True,
             "temp_path": "/data/downloads/incomplete",
             "bypass_auth_subnet_whitelist_enabled": True,
             "bypass_auth_subnet_whitelist": "172.16.0.0/12"}
    if PASSWORD:
        prefs.update(web_ui_username=USER, web_ui_password=PASSWORD)
    r = s.http("POST", f"{QBIT}/api/v2/app/setPreferences", headers=h, form={"json": json.dumps(prefs)})
    if not r.ok:
        return result("qBittorrent", False, f"innstillinger feilet ({r.status})")
    ok("nedlastingsmapper, passord og innlogging for containere satt")

    r = s.http("GET", f"{QBIT}/api/v2/torrents/categories", headers=h)
    cats = (r.json() or {}) if r.ok else {}
    for cat, path in (("tv", "/data/downloads/tv"), ("movies", "/data/downloads/movies")):
        if cat not in cats:
            s.http("POST", f"{QBIT}/api/v2/torrents/createCategory", headers=h,
                   form={"category": cat, "savePath": path})
    ok("kategoriene tv og movies finnes")
    STATE["qbit_ok"] = True
    result("qBittorrent", True, "klar")


def qbit_pbkdf2(password):
    """qBittorrent sitt passordformat: PBKDF2-HMAC-SHA512, 100 000 runder, 16 byte salt."""
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 100000, 64)
    return f"@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(key).decode()})"


def qbit_write_config():
    paths = [os.path.join(d, f) for d, _, files in os.walk(f"{CFG}/qbittorrent")
             for f in files if f == "qBittorrent.conf"]
    if not paths:
        return False
    docker_action("qbittorrent", "stop")      # qBittorrent skriver over fila når den avslutter
    for path in paths:
        qbit_patch(path)
        log(f"  skrev {path.replace(CFG, 'config')}")
    docker_action("qbittorrent", "start")
    return True


def qbit_patch(path):
    txt = read(path)
    sets = {"WebUI\\AuthSubnetWhitelistEnabled": "true",
            "WebUI\\AuthSubnetWhitelist": "172.16.0.0/12",
            "WebUI\\MaxAuthenticationFailCount": "10"}
    if PASSWORD:
        sets.update({"WebUI\\Username": USER, "WebUI\\Password_PBKDF2": f'"{qbit_pbkdf2(PASSWORD)}"'})
    for k, v in sets.items():
        txt = ini_set(txt, "Preferences", k, v, sep="=")
    write(path, txt, owner=(PUID, PGID))


# =========================================================================== NZBGet
def setup_nzbget():
    base = "http://nzbget:6789"
    if not wait_http(base + "/", 180, "NZBGet"):
        return result("NZBGet", False, "svarer ikke")

    def rpc(method, params, creds):
        auth = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
        r = http("POST", base + "/jsonrpc", headers={"Authorization": f"Basic {auth}"},
                 json_body={"method": method, "params": params, "id": 1}, timeout=60)
        return r.json().get("result") if r.ok and r.json() else None

    creds = next((c for c in ((USER, PASSWORD), ("nzbget", "tegbzn6789"))
                  if c[1] and rpc("version", [], c) is not None), None)
    if not creds:
        return result("NZBGet", False, "kom ikke inn (sjekk ADMIN_PASSWORD i .env)")
    cfg = rpc("config", [], creds) or []
    if not cfg:
        return result("NZBGet", False, "fikk ikke lest innstillingene")
    host = env("USENET_HOST")
    port = env("USENET_PORT", "563")
    want = {
        "DestDir": "/data/downloads/usenet/completed",
        "InterDir": "/data/downloads/usenet/intermediate",
        "Category1.Name": "movies", "Category1.DestDir": "",
        "Category2.Name": "tv", "Category2.DestDir": "",
        "PostStrategy": "sequential",      # én utpakking om gangen – skåner prosessoren
        "HealthCheck": "delete",           # fjernet/ødelagt utgivelse: avbryt, så Sonarr/Radarr tar neste
    }
    if PASSWORD:
        want.update(ControlUsername=USER, ControlPassword=PASSWORD)
    if host and env("USENET_USER"):
        want.update({"Server1.Active": "yes", "Server1.Name": host, "Server1.Level": "0",
                     "Server1.Host": host, "Server1.Port": port,
                     "Server1.Encryption": "yes" if port in ("563", "443", "80", "81") else "no",
                     "Server1.Username": env("USENET_USER"), "Server1.Password": env("USENET_PASSWORD"),
                     "Server1.Connections": env("USENET_CONNECTIONS", "30")})
    else:
        TODO.append("NZBGet: legg inn Usenet-serveren under Settings → News-Servers (eller kjør veiviseren).")
    STATE["nzbget_ok"] = True
    current = {o["Name"]: o["Value"] for o in cfg}
    if all(current.get(k) == v for k, v in want.items()):
        return result("NZBGet", True, "klar (uendret)")
    opts = [{"Name": o["Name"], "Value": want.pop(o["Name"], o["Value"])} for o in cfg]
    opts += [{"Name": k, "Value": v} for k, v in want.items()]
    if not rpc("saveconfig", [opts], creds):
        return result("NZBGet", False, "lagring av innstillinger feilet")
    rpc("reload", [], creds)
    ok(f"server {host or '(ikke satt)'}, mapper, kategorier movies/tv")
    result("NZBGet", True, "klar")


# =========================================================================== Prowlarr
def setup_prowlarr():
    key = arr_key("prowlarr")
    if not key:
        return result("Prowlarr", False, "fant ikke API-nøkkel")
    STATE["prowlarr_key"] = key
    p = Arr("prowlarr", "http://prowlarr:9696", key, "v1")
    wait_http("http://prowlarr:9696/ping", 180, "Prowlarr")
    p.set_auth()
    ok("innlogging satt")

    tags = p.get("tag")
    tag = next((t for t in tags if t["label"] == "flaresolverr"), None) or p.post("tag", {"label": "flaresolverr"})
    p.upsert("indexerproxy", "FlareSolverr", "FlareSolverr",
             {"host": "http://flaresolverr:8191/", "requestTimeout": 60}, {"tags": [tag["id"]]})
    ok("FlareSolverr-proxy med taggen «flaresolverr»")

    for app, port in (("Sonarr", 8989), ("Radarr", 7878)):
        k = STATE.get(f"{app.lower()}_key")
        if not k:
            continue
        v = p.upsert("applications", app, app,
                     {"prowlarrUrl": "http://prowlarr:9696", "baseUrl": f"http://{app.lower()}:{port}",
                      "apiKey": k}, {"syncLevel": "fullSync"})
        ok(f"{app} {v} – indeksere synkes automatisk dit")

    tagged = 0
    for ix in p.get("indexer"):
        if tag["id"] not in ix.get("tags", []):
            ix.setdefault("tags", []).append(tag["id"])
            try:
                p.put(f"indexer/{ix['id']}?forceSave=true", ix)
                tagged += 1
            except RuntimeError:
                pass
    indexers = len(p.get("indexer"))
    if tagged:
        ok(f"{tagged} indeksere fikk FlareSolverr-taggen")
    if USENET:
        TODO.append("Prowlarr: legg til NZB-indeksere (Indexers → Add Indexer, søk opp indekseren, lim "
                    "inn API-nøkkelen fra kontoen din der). De synkes automatisk til Sonarr og Radarr.")
    if not indexers:
        TODO.append("Prowlarr: legg til indeksere (Indexers → Add Indexer). De kobles automatisk til "
                    "Sonarr og Radarr. Kjør «docker compose run --rm autoconfig» etterpå, så får de "
                    "FlareSolverr-taggen.")
    result("Prowlarr", True, f"klar ({indexers} indeksere)")


# =========================================================================== Bazarr
def setup_bazarr():
    path = f"{CFG}/bazarr/config/config.yaml"
    if not wait_file(path, 180):
        return result("Bazarr", False, "fant ikke config.yaml")
    m = re.search(r"^auth:\s*\n(?:[ \t]+.*\n)*?[ \t]+apikey:\s*['\"]?([A-Za-z0-9]+)", read(path), re.M)
    if not m:
        return result("Bazarr", False, "fant ikke API-nøkkel")
    key = STATE["bazarr_key"] = m.group(1)
    wait_http("http://bazarr:6767/api/system/status", 180, "Bazarr", headers={"X-API-KEY": key})

    langs = [l.strip() for l in env("SUBTITLE_LANGS", "no,en").split(",") if l.strip()]
    profile = [{"profileId": 1, "name": " + ".join(langs), "cutoff": None, "mustContain": [],
                "mustNotContain": [], "originalFormat": False,
                "items": [{"id": i + 1, "language": l, "audio_exclude": "False", "hi": "False",
                           "forced": "False"} for i, l in enumerate(langs)]}]
    form = {
        "settings-general-use_sonarr": "true", "settings-general-use_radarr": "true",
        "settings-sonarr-ip": "sonarr", "settings-sonarr-port": "8989", "settings-sonarr-base_url": "/",
        "settings-sonarr-apikey": STATE.get("sonarr_key", ""), "settings-sonarr-ssl": "false",
        "settings-radarr-ip": "radarr", "settings-radarr-port": "7878", "settings-radarr-base_url": "/",
        "settings-radarr-apikey": STATE.get("radarr_key", ""), "settings-radarr-ssl": "false",
        "languages-enabled": langs, "languages-profiles": json.dumps(profile),
        "settings-general-serie_default_enabled": "true", "settings-general-serie_default_profile": "1",
        "settings-general-movie_default_enabled": "true", "settings-general-movie_default_profile": "1",
    }
    if PASSWORD:
        form.update({"settings-auth-type": "form", "settings-auth-username": USER,
                     "settings-auth-password": PASSWORD})
    r = http("POST", "http://bazarr:6767/api/system/settings", headers={"X-API-KEY": key}, form=form)
    if not r.ok:
        return result("Bazarr", False, f"innstillinger feilet ({r.status})")
    ok(f"koblet til Sonarr og Radarr, språkprofil: {' + '.join(langs)}")
    TODO.append("Bazarr: velg undertekstkilder under Settings → Providers (f.eks. OpenSubtitles.com, "
                "Podnapisi, Addic7ed – noen krever gratis konto).")
    result("Bazarr", True, "klar")


# =========================================================================== Whisper
def setup_whisper():
    """Kobler Whisper (AI-undertekster) til Bazarr som undertekstkilde."""
    key = STATE.get("bazarr_key")
    if not key:
        return result("Whisper", False, "Bazarr er ikke satt opp")
    hdr = {"X-API-KEY": key}
    if not wait_http(f"{WHISPER_URL}/docs", 300 if WHISPER != "ekstern" else 30, "Whisper"):
        msg = (f"svarer ikke på {WHISPER_URL} – er Whisper startet på den andre maskinen?"
               if WHISPER == "ekstern" else "svarer ikke – se «docker logs whisper»")
        return result("Whisper", False, msg)
    cur = http("GET", "http://bazarr:6767/api/system/settings", headers=hdr).json() or {}
    providers = list((cur.get("general") or {}).get("enabled_providers") or [])
    if "whisperai" not in providers:
        providers.append("whisperai")
    form = {
        "settings-general-enabled_providers": providers,
        "settings-whisperai-endpoint": WHISPER_URL,
        # Prosessoren bruker gjerne like lang tid som filmen varer, eller mer. Gi den god tid.
        "settings-whisperai-timeout": "3600" if WHISPER in ("gpu", "ekstern") else "14400",
        "settings-whisperai-loglevel": "INFO",
        "settings-whisperai-pass_video_name": "false",
        # Les språket på lydsporene, så Whisper vet hva den lytter til og slipper å gjette.
        "settings-general-parse_embedded_audio_track": "true",
    }
    if WHISPER_AUTO:
        # Whisper-tekster får alltid fast poengsum: 61 % for episoder og 33 % for filmer.
        form.update({"settings-general-minimum_score": "60", "settings-general-minimum_score_movie": "33"})
    r = http("POST", "http://bazarr:6767/api/system/settings", headers=hdr, form=form)
    if not r.ok:
        return result("Whisper", False, f"kunne ikke legge Whisper inn i Bazarr ({r.status})")
    where = {"cpu": "prosessoren", "gpu": "skjermkortet", "ekstern": WHISPER_URL}.get(WHISPER, WHISPER)
    ok(f"lagt inn i Bazarr som undertekstkilde (kjører på {where}, modell {env('WHISPER_MODEL', 'turbo')})")
    if WHISPER_AUTO:
        result("Whisper", True, "lager undertekster automatisk når ingen andre kilder har dem")
    else:
        result("Whisper", True, "klar – velg «Whisper» i manuelt søk i Bazarr")
        TODO.append("Whisper: i Bazarr, åpne en film eller episode → søk manuelt (forstørrelsesglass) → "
                    "velg treffet fra «whisperai». Første gang lastes AI-modellen ned (1–3 GB).")


# =========================================================================== Recyclarr
def setup_recyclarr():
    if not (STATE.get("sonarr_key") and STATE.get("radarr_key")):
        return result("Recyclarr", False, "mangler Sonarr/Radarr-nøkler")
    d = f"{CFG}/recyclarr"
    write(f"{d}/secrets.yml", f"sonarr_url: http://sonarr:8989\nsonarr_apikey: {STATE['sonarr_key']}\n"
                              f"radarr_url: http://radarr:7878\nradarr_apikey: {STATE['radarr_key']}\n",
          0o600, (PUID, PGID))
    if not wait_container("recyclarr", 60):
        return result("Recyclarr", False, "containeren kjører ikke")
    for tpl, app in ((env("RECYCLARR_SONARR_TEMPLATE", "web-1080p"), "sonarr"),
                     (env("RECYCLARR_RADARR_TEMPLATE", "hd-bluray-web"), "radarr")):
        f = f"{d}/configs/{tpl}.yml"
        if not os.path.isfile(f):
            code, out = docker_exec("recyclarr", ["recyclarr", "config", "create", "--template", tpl])
            if not os.path.isfile(f):
                warn(f"malen {tpl} ble ikke laget: {out.strip()[-300:]}")
                continue
        txt = read(f)
        txt = re.sub(r"(?m)^(\s*)base_url:.*$", rf"\1base_url: !secret {app}_url", txt)
        txt = re.sub(r"(?m)^(\s*)api_key:.*$", rf"\1api_key: !secret {app}_apikey", txt)
        write(f, txt, owner=(PUID, PGID))
        ok(f"mal {tpl} → {app}")
    log("  synker TRaSH-profiler (kan ta et minutt) …")
    code, out = docker_exec("recyclarr", ["recyclarr", "sync"], timeout=900)
    if code == 0:
        result("Recyclarr", True, "kvalitetsprofiler synket – oppdateres automatisk hver natt")
    else:
        result("Recyclarr", False, "synk feilet – se «docker logs recyclarr»")
        log("    " + "\n    ".join(out.strip().splitlines()[-8:]))


# =========================================================================== Kvalitet
def setup_quality():
    minres = int(env("MIN_RESOLUTION", "1080"))
    quality.enforce(minres)
    result("Kvalitet", True, f"bare {minres}p eller bedre (sjekkes hvert 15. min)")


# =========================================================================== Seerr
def pick_profile(arr, preferred):
    profiles = arr.get("qualityprofile")
    for name in preferred:
        for p in profiles:
            if p["name"].lower() == name.lower():
                return p
    return profiles[0]


def seerr_libraries(s, base, h, only_if_none=False, server=None):
    """Synker bibliotekene fra Jellyfin/Plex i Seerr og slår dem på. Returnerer antall, eller None ved feil."""
    server = server or MEDIA
    if only_if_none:
        cur = s.http("GET", f"{base}/settings/{server}/library", headers=h).json()
        if isinstance(cur, list) and any(isinstance(l, dict) and l.get("enabled") for l in cur):
            return 0
    libs, last, new_api = [], None, True
    for _ in range(10):
        r = s.http("POST", f"{base}/settings/{server}/library/sync", headers=h, timeout=60)   # Seerr 3.5+
        if r.status in (404, 405):
            new_api = False
            r = s.http("GET", f"{base}/settings/{server}/library?sync=true", headers=h, timeout=60)
        last = r
        data = r.json()
        if isinstance(data, list) and data:
            libs = [l for l in data if isinstance(l, dict) and "id" in l]
            break
        time.sleep(6)
    if not libs:
        warn(f"fant ingen bibliotek i Seerr ({last.status}: {last.text[:120]})")
        return None
    enabled = 0
    if new_api:
        for l in libs:
            enabled += s.http("PUT", f"{base}/settings/{server}/library/{l['id']}", headers=h,
                              json_body={**l, "enabled": True}).ok
    if not enabled:
        s.http("GET", f"{base}/settings/{server}/library?enable=" + ",".join(str(l["id"]) for l in libs), headers=h)
        enabled = len(libs)
    if not only_if_none:
        ok(f"{enabled} bibliotek aktivert")
    return enabled


def setup_seerr():
    base = "http://seerr:5055/api/v1"
    if not wait_http(f"{base}/status", 240, "Seerr"):
        return result("Seerr", False, "svarer ikke")
    s = Session()
    pub = s.http("GET", f"{base}/settings/public").json() or {}
    if pub.get("initialized"):
        main = json.loads(read(f"{CFG}/seerr/settings.json") or "{}").get("main") or {}
        key = main.get("apiKey", "")
        kind = {1: "plex", 2: "jellyfin", 3: "emby"}.get(main.get("mediaServerType"))
        if kind and kind != MEDIA:
            TODO.append(f"Seerr er satt opp mot {kind.title()}. For å bytte til {MEDIA.title()}: "
                        "«docker compose stop seerr», slett mappa config/seerr, og kjør «docker compose up -d» "
                        "og «docker compose run --rm autoconfig». Ønskene som er gjort, forsvinner.")
            return result("Seerr", False, f"koblet til {kind.title()}, ikke {MEDIA.title()}")
        if key:
            h = {"X-Api-Key": key}
            if STATE.get("plex_token") or STATE.get("jf_key"):
                n = seerr_libraries(s, base, h, only_if_none=True)
                if n:
                    ok(f"{n} bibliotek aktivert")
            for app, folder in (("radarr", f"/data/{MOVIES}"), ("sonarr", f"/data/{TV}")):
                servers = s.http("GET", f"{base}/settings/{app}", headers=h).json()
                for srv in servers if isinstance(servers, list) else []:
                    upd = {}
                    if srv.get("activeDirectory") != folder:
                        upd["activeDirectory"] = folder
                    if app == "sonarr" and srv.get("activeAnimeDirectory") not in (None, folder):
                        upd["activeAnimeDirectory"] = folder
                    if upd and s.http("PUT", f"{base}/settings/{app}/{srv['id']}", headers=h,
                                      json_body={**srv, **upd}).ok:
                        ok(f"{app}-mappe i Seerr rettet til {folder}")
        return result("Seerr", True, "allerede satt opp")
    if MEDIA == "jellyfin":
        if not (STATE.get("jf_key") and PASSWORD):
            TODO.append(f"Seerr: åpne http://{DASH}:5055 og logg inn med Jellyfin for å sette den opp.")
            return result("Seerr", False, "venter på Jellyfin")
        port = urllib.parse.urlparse(JF).port or 8096
        body = {"username": USER, "password": PASSWORD, "hostname": JF_HOST, "port": port, "useSsl": False,
                "urlBase": "", "email": f"{USER}@film-stack.local", "serverType": 2}
        r = s.http("POST", f"{base}/auth/jellyfin", json_body=body)
        if not r.ok:                     # eldre versjoner ville ha hele adressen i hostname
            r = s.http("POST", f"{base}/auth/jellyfin", json_body={**body, "hostname": JF})
        if not r.ok:
            TODO.append(f"Seerr: åpne http://{DASH}:5055, velg Jellyfin og logg inn med {USER} (adresse {JF}).")
            return result("Seerr", False, f"Jellyfin-innlogging feilet ({r.status} {r.text[:120]})")
        ok("administrator = Jellyfin-brukeren din")
        if seerr_libraries(s, base, {}) is None:
            TODO.append(f"Seerr: åpne http://{DASH}:5055 → Settings → Jellyfin, trykk «Sync Libraries» og huk av bibliotekene.")
    else:
        if not (ACCOUNT_TOKEN and STATE.get("plex_token")):
            TODO.append("Seerr: åpne http://<server>:5055 og logg inn med Plex for å sette den opp.")
            return result("Seerr", False, "trenger Plex-innlogging (kjør veiviseren og logg inn i Plex)")
        r = s.http("POST", f"{base}/auth/plex", json_body={"authToken": ACCOUNT_TOKEN})
        if not r.ok:
            return result("Seerr", False, f"Plex-innlogging feilet ({r.status})")
        ok("administrator = Plex-kontoen din")
        r = s.http("POST", f"{base}/settings/plex", json_body={"ip": PLEX_HOST, "port": 32400, "useSsl": False})
        if not r.ok:
            warn(f"Plex-tilkobling: {r.status} {r.text[:150]}")
        if seerr_libraries(s, base, {}) is None:
            TODO.append("Seerr: åpne http://<server>:5055 → Settings → Plex, trykk «Sync Libraries» og huk av bibliotekene.")

    if STATE.get("radarr_key"):
        a = Arr("radarr", "http://radarr:7878", STATE["radarr_key"])
        p = pick_profile(a, ["HD Bluray + WEB", "HD-1080p", "Any"])
        r = s.http("POST", f"{base}/settings/radarr", json_body={
            "name": "Radarr", "hostname": "radarr", "port": 7878, "apiKey": STATE["radarr_key"],
            "useSsl": False, "baseUrl": "", "activeProfileId": p["id"], "activeProfileName": p["name"],
            "activeDirectory": f"/data/{MOVIES}", "is4k": False, "minimumAvailability": "released",
            "tags": [], "isDefault": True, "externalUrl": "", "syncEnabled": False, "preventSearch": False})
        ok(f"Radarr koblet til (profil {p['name']})") if r.ok else warn(f"Radarr: {r.status}")
    if STATE.get("sonarr_key"):
        a = Arr("sonarr", "http://sonarr:8989", STATE["sonarr_key"])
        p = pick_profile(a, ["WEB-1080p", "HD-1080p", "Any"])
        r = s.http("POST", f"{base}/settings/sonarr", json_body={
            "name": "Sonarr", "hostname": "sonarr", "port": 8989, "apiKey": STATE["sonarr_key"],
            "useSsl": False, "baseUrl": "", "activeProfileId": p["id"], "activeProfileName": p["name"],
            "activeDirectory": f"/data/{TV}", "activeAnimeProfileId": p["id"],
            "activeAnimeProfileName": p["name"], "activeAnimeDirectory": f"/data/{TV}",
            "seriesType": "standard", "animeSeriesType": "anime", "tags": [], "animeTags": [],
            "is4k": False, "isDefault": True, "enableSeasonFolders": True, "externalUrl": "",
            "syncEnabled": False, "preventSearch": False})
        ok(f"Sonarr koblet til (profil {p['name']})") if r.ok else warn(f"Sonarr: {r.status}")

    r = s.http("POST", f"{base}/settings/initialize")
    result("Seerr", r.ok, f"klar – logg inn med {'Jellyfin' if MEDIA == 'jellyfin' else 'Plex'}"
           if r.ok else f"fullføring feilet ({r.status})")


# =========================================================================== Tautulli
def setup_tautulli():
    path = f"{CFG}/tautulli/config.ini"
    if not wait_file(path, 180):
        return result("Tautulli", False, "fant ikke config.ini")
    txt = read(path)
    if STATE.get("plex_token") and ini_get(txt, "PMS", "pms_token") != STATE["plex_token"]:
        docker_action("tautulli", "stop")
        txt = read(path)
        for k, v in {"pms_ip": PLEX_HOST, "pms_port": "32400", "pms_token": STATE["plex_token"],
                     "pms_identifier": STATE.get("plex_id", ""), "pms_name": STATE.get("plex_name", ""),
                     "pms_url": PLEX_URL, "pms_ssl": "0", "pms_is_remote": "0"}.items():
            txt = ini_set(txt, "PMS", k, v)
        txt = ini_set(txt, "General", "first_run_complete", "1")
        txt = ini_set(txt, "General", "api_enabled", "1")
        write(path, txt)
        docker_action("tautulli", "start")
        ok("koblet til Plex")
    if not ini_get(read(path), "General", "http_password").strip('"'):
        TODO.append("Tautulli: sett passord under Settings → Web Interface (Tautulli krypterer det selv), "
                    "og huk av «Allow Plex Admin» for innlogging med Plex.")
    STATE["tautulli_key"] = ini_get(read(path), "General", "api_key")
    if not STATE.get("plex_token"):
        return result("Tautulli", False, "venter på Plex-innlogging")
    result("Tautulli", True, "klar")


# =========================================================================== Prefetcharr
def setup_prefetcharr():
    path = f"{CFG}/prefetcharr/config.toml"
    server = ("Jellyfin", JF, STATE.get("jf_key")) if MEDIA == "jellyfin" else ("Plex", PLEX_URL, STATE.get("plex_token"))
    if not (server[2] and STATE.get("sonarr_key")):
        docker_action("prefetcharr", "stop")
        return result("Prefetcharr", False, f"stoppet – trenger {server[0]} og Sonarr")
    write(path, f'''# Laget av autoconfig. Endringer: rediger og kjør «docker compose restart prefetcharr».
interval = 900            # sjekk hvert 15. minutt hva som spilles
log_dir = "/log"
log_level = "Info"
prefetch_num = 2          # sørg for at 2 episoder fram er tilgjengelige
request_seasons = true    # be om hele sesonger (foretrekker sesongpakker)
connection_retries = 6

[media_server]
type = "{server[0]}"
url = "{server[1]}"
api_key = "{server[2]}"

[sonarr]
url = "http://sonarr:8989"
api_key = "{STATE['sonarr_key']}"
# exclude_tag = "no_prefetch"
''', 0o600, (PUID, PGID))
    docker_action("prefetcharr", "restart")
    result("Prefetcharr", True, "klar – henter neste episoder når noen ser en serie")


# =========================================================================== Homepage
def setup_homepage():
    seerr_key = (json.loads(read(f"{CFG}/seerr/settings.json") or "{}").get("main", {}) or {}).get("apiKey", "")
    k = {**STATE, "seerr_key": seerr_key}

    def svc(name, icon, port, desc, cont=None, widget=None, href=True):
        lines = [f"    - {name}:", f"        icon: {icon}", f"        description: {desc}"]
        if href:
            lines.append(f"        href: http://{DASH}:{port}" + ("/web" if name == "Plex" else ""))
        if cont:
            lines += ["        server: my-docker", f"        container: {cont}"]
        if widget:
            lines.append("        widget:")
            lines += [f"          {a}: {json.dumps(b)}" for a, b in widget.items()]
        return "\n".join(lines)

    def w(typ, url, key=None, **extra):
        if key is None and not extra:
            return None
        d = {"type": typ, "url": url}
        if key:
            d["key"] = key
        d.update(extra)
        return d

    if MEDIA == "jellyfin":
        server = svc("Jellyfin", "jellyfin.png", 8096, "Filmer og serier", "jellyfin",
                     w("jellyfin", JF, k.get("jf_key"), enableBlocks=True, enableNowPlaying=True) if k.get("jf_key") else None)
    else:
        server = svc("Plex", "plex.png", 32400, "Filmer og serier", "plex",
                     w("plex", PLEX_URL, k.get("plex_token")) if k.get("plex_token") else None)
    groups = {
        "Se på": [
            server,
            svc("Seerr", "overseerr.png", 5055, "Ønsk deg filmer og serier", "seerr",
                w("overseerr", "http://seerr:5055", seerr_key) if seerr_key else None),
        ] + ([svc("Tautulli", "tautulli.png", 8181, "Hvem ser hva", "tautulli",
                  w("tautulli", "http://tautulli:8181", k.get("tautulli_key")) if k.get("tautulli_key") else None)]
             if MEDIA == "plex" else []),
        "Bibliotek": [
            svc("Sonarr", "sonarr.png", 8989, "TV-serier", "sonarr",
                w("sonarr", "http://sonarr:8989", k.get("sonarr_key")) if k.get("sonarr_key") else None),
            svc("Radarr", "radarr.png", 7878, "Filmer", "radarr",
                w("radarr", "http://radarr:7878", k.get("radarr_key")) if k.get("radarr_key") else None),
            svc("Bazarr", "bazarr.png", 6767, "Undertekster", "bazarr",
                w("bazarr", "http://bazarr:6767", k.get("bazarr_key")) if k.get("bazarr_key") else None),
        ],
        "Nedlasting": [
            svc("qBittorrent", "qbittorrent.png", 8080, "Torrent" + (" via VPN" if "gluetun" in QBIT else ""),
                "qbittorrent", w("qbittorrent", QBIT, None, username=USER, password=PASSWORD) if PASSWORD else None),
            svc("Prowlarr", "prowlarr.png", 9696, "Indeksere", "prowlarr",
                w("prowlarr", "http://prowlarr:9696", k.get("prowlarr_key")) if k.get("prowlarr_key") else None),
        ],
        "Vedlikehold": [
            svc("Kontrollpanel", "mdi-tune-variant", 3010, "Status, innstillinger og løsninger for hele stacken", "throttle",
                {"type": "customapi", "url": "http://throttle:3010/api/status", "refreshInterval": 10000,
                 "mappings": [{"field": "helse", "label": "Status"},
                              {"field": "status", "label": "Kjøling"},
                              {"field": "temp", "label": "Temp", "format": "number", "suffix": " °C"},
                              {"field": "cpu", "label": "Prosessor", "format": "number", "suffix": " %"}]}),
            svc("Recyclarr", "recyclarr.png", 0, "TRaSH-profiler, synker hver natt", "recyclarr", href=False),
            svc("Prefetcharr", "mdi-skip-next", 0, "Henter neste episoder", "prefetcharr", href=False),
            svc("FlareSolverr", "flaresolverr.png", 0, "Cloudflare-hjelper", "flaresolverr", href=False),
        ],
    }
    if "gluetun" in QBIT:
        groups["Nedlasting"].insert(1, svc("Gluetun", "gluetun.png", 0, "VPN-tunnel", "gluetun", href=False))
    if USENET:
        groups["Nedlasting"].insert(1, svc("NZBGet", "nzbget.png", 6789, "Usenet", "nzbget",
                                           w("nzbget", "http://nzbget:6789", None, username=USER,
                                             password=PASSWORD) if PASSWORD else None))
    if "wizarr" in PROFILES:
        groups["Se på"].append(svc("Wizarr", "wizarr.png", 5690, f"Invitasjoner til {'Jellyfin' if MEDIA == 'jellyfin' else 'Plex'}", "wizarr"))
    if "autobrr" in PROFILES:
        groups["Nedlasting"].append(svc("Autobrr", "autobrr.png", 7474, "IRC-annonser", "autobrr"))

    out = ["# Laget av autoconfig – kjøres på nytt med «docker compose run --rm autoconfig»", ""]
    for g, items in groups.items():
        out.append(f"- {g}:")
        out += items
    write(f"{CFG}/homepage/services.yaml", "\n".join(out) + "\n", 0o600, (PUID, PGID))
    write(f"{CFG}/homepage/widgets.yaml", WIDGETS, owner=(PUID, PGID))
    result("Homepage", True, f"dashboard på http://{DASH}:3000")


WIDGETS = """# Laget av autoconfig. Belastning, temperatur og minne for hele maskinen: se kortet «Kontrollpanel».
- resources:
    label: Mediedisk
    disk: /mediadisk
    expanded: true

- resources:
    label: Oppetid
    uptime: true

- datetime:
    text_size: xl
    format:
      dateStyle: long
      timeStyle: short
      hourCycle: h23

- search:
    provider: duckduckgo
    target: _blank
"""


# =========================================================================== oppsummering
def summary():
    lines = ["", "═" * 66, "  OPPSUMMERING", "═" * 66]
    for app, good, msg in RESULTS:
        lines.append(f"  {'✔' if good else '⚠'} {app:<12} {msg}")
    lines += ["", f"  Dashboard:  http://{DASH}:3000"]
    if PASSWORD:
        lines.append(f"  Innlogging: {USER} / {PASSWORD}" + ("   (Plex og Seerr: Plex-kontoen din)" if MEDIA == "plex" else ""))
    if TODO:
        lines += ["", "  Gjør dette for hånd:"]
        lines += [f"   • {t}" for t in dict.fromkeys(TODO)]
    lines += ["", "  Sjekk at alt virker (VPN, nedlastere, mediaserver):  docker compose run --rm sjekk",
              "  Kjør oppsettet på nytt når som helst:  docker compose run --rm autoconfig", ""]
    text = "\n".join(lines)
    log(text)
    write(f"{CFG}/OPPSUMMERING.txt", text.replace(f" / {PASSWORD}", " / (se .env)") if PASSWORD else text,
          owner=(PUID, PGID))


def main():
    log("autoconfig: setter opp og kobler sammen appene. Første gang tar dette 2–5 minutter.")
    log(f"Mediaserver: {'Jellyfin' if MEDIA == 'jellyfin' else 'Plex'}   Mediemapper: /data/{MOVIES} (filmer), /data/{TV} (serier)")
    if MEDIA == "jellyfin":
        run("Jellyfin", setup_jellyfin)
    else:
        run("Plex", setup_plex)
    run("qBittorrent", setup_qbit)
    if USENET:
        run("NZBGet", setup_nzbget)
    run("Sonarr", lambda: setup_arr("sonarr", 8989, "tv"))
    run("Radarr", lambda: setup_arr("radarr", 7878, "movie"))
    run("Prowlarr", setup_prowlarr)
    run("Bazarr", setup_bazarr)
    if WHISPER != "av":
        run("Whisper", setup_whisper)
    run("Recyclarr", setup_recyclarr)
    run("Kvalitet", setup_quality)
    run("Seerr", setup_seerr)
    if MEDIA == "plex":
        run("Tautulli", setup_tautulli)
    run("Prefetcharr", setup_prefetcharr)
    run("Homepage", setup_homepage)
    if "wizarr" in PROFILES:
        TODO.append(f"Wizarr: åpne http://{DASH}:5690 og fullfør oppsettet (koble til {'Jellyfin' if MEDIA == 'jellyfin' else 'Plex'}).")
    if "autobrr" in PROFILES:
        TODO.append(f"Autobrr: åpne http://{DASH}:7474 og legg inn trackerne dine.")
    summary()


if __name__ == "__main__":
    main()
    sys.exit(0)
