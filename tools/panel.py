"""Kontrollpanel for Film-stack: status (grønn/gul/rød) med forklaringer og løsninger, og innstillinger.

Kjører i kjøletjenesten (throttle.py) på http://<server>:3010 og bruker de samme sjekkene som
«docker compose run --rm sjekk». Innstillingene lagres i config/panel/innstillinger.json og
legges inn i appene via API-ene deres.

Løsninger:
  • trygge (starte en app på nytt, stoppe qBittorrent ved VPN-lekkasje, starte manglende tjenester)
    kjøres av seg selv, høyst to ganger per seks timer per feil
  • alt som endrer innstillinger kjøres først når du trykker «Fiks»
"""
import glob
import io
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import zipfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import autoconfig as A  # noqa: E402
import quality  # noqa: E402
import sjekk as SJ  # noqa: E402
from common import (CFG, DATA, demux, docker, docker_action, docker_logs, env, http, log,  # noqa: E402
                    plex_pref, read, write, xml_apikey)

DIR = f"{CFG}/panel"
SETTINGS_FILE = f"{DIR}/innstillinger.json"
STATE_FILE = f"{DIR}/tilstand.json"
PROJECT = env("COMPOSE_PROJECT_NAME", "film-stack")
RUNNER = f"{PROJECT}-oppdatering"
STARTED = time.time()
GRACE = 300                      # ingen automatiske løsninger de første 5 minuttene etter oppstart
AUTO_MAX, AUTO_WINDOW = 2, 6 * 3600
CHECK_EVERY = 15 * 60
DEBUG_HOURS = 24
DAGER = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]

DEFAULTS = {
    "kvalitet": {"min_opplosning": int(env("MIN_RESOLUTION", "1080") or 1080)},
    "disk": {"varsel_gb": 50, "pause_gb": 15},
    "fart": {"ned_mb": 0, "opp_mb": 0, "rolig_ned_mb": 5, "rolig_opp_mb": 1,
             "plan": False, "plan_fra": "07:00", "plan_til": "23:00"},
    "oppdatering": {"modus": "ukentlig", "dag": 6, "klokke": "04:00", "vent_paa_avspilling": True},
    "automatikk": True,
}

LOCK = threading.RLock()
CHECK_LOCK = threading.Lock()
SETTINGS = {}
ST = {
    "rows": [], "checked": 0, "checking": False,
    "events": [],                 # [{tid, tekst, type}]
    "attempts": {},               # fiks-id -> [tidspunkter]
    "blocked": {},                # container -> grunn (stoppet med vilje, startes ikke automatisk)
    "debug_til": 0, "debug_resultat": {},
    "disk_pause": False,
    "oppdatering": {"siste": 0, "resultat": "", "logg": "", "kjorer": False, "utsatt_til": 0},
    "kvalitet": {},
}


# =========================================================================== lagring
def _merge(base, over):
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def load():
    try:
        SETTINGS.update(_merge(DEFAULTS, json.loads(read(SETTINGS_FILE) or "{}")))
    except ValueError:
        SETTINGS.update(_merge(DEFAULTS, {}))
    try:
        saved = json.loads(read(STATE_FILE) or "{}")
        for k in ("events", "attempts", "blocked", "debug_til", "disk_pause", "oppdatering", "kvalitet"):
            if k in saved:
                ST[k] = saved[k] if not isinstance(ST[k], dict) else {**ST[k], **saved[k]}
    except ValueError:
        pass
    ST["oppdatering"]["kjorer"] = False
    SJ.FREE_WARN_GB = float(SETTINGS["disk"]["varsel_gb"])


def save():
    with LOCK:
        write(SETTINGS_FILE, json.dumps(SETTINGS, indent=2, ensure_ascii=False))
        keep = {k: ST[k] for k in ("events", "attempts", "blocked", "debug_til", "disk_pause", "oppdatering", "kvalitet")}
        write(STATE_FILE, json.dumps(keep, ensure_ascii=False))


def event(text, kind="system"):
    with LOCK:
        ST["events"] = ([{"tid": time.time(), "tekst": text, "type": kind}] + ST["events"])[:200]
    log(f"{time.strftime('%H:%M')} panel: {text}")
    save()


def minres():
    return int(SETTINGS.get("kvalitet", {}).get("min_opplosning") or 1080)


# =========================================================================== status og løsninger
NIVA = {"ok": "grønn", "warn": "gul", "fail": "rød"}


def find_fix(group, level, text, advice):
    """Hvilken løsning passer til et funn? auto=True betyr at den er trygg å kjøre av seg selv."""
    if level == "ok":
        return None
    m = re.match(r"(\S+) (kjører ikke|kjører, men er ikke frisk)", text)
    if group == "Containere" and m:
        name = m.group(1)
        if name in ST["blocked"]:
            return {"id": f"start:{name}", "navn": f"Start {name} igjen", "auto": False,
                    "forklaring": f"Stoppet med vilje: {ST['blocked'][name]}"}
        return {"id": f"restart:{name}", "navn": f"Start {name} på nytt", "auto": True}
    if group == "Containere" and "finnes ikke" in text:
        return {"id": "compose:up", "navn": "Start alle tjenestene", "auto": True}
    if group == "VPN" and "LEKKASJE" in text:
        return {"id": "stop:qbittorrent", "navn": "Stopp qBittorrent", "auto": True}
    if group == "VPN" and "kommer ikke ut" in text:
        return {"id": "restart:gluetun", "navn": "Start VPN-en på nytt", "auto": True}
    if group == "qBittorrent" and "ingen nettforbindelse" in text:
        return {"id": "restart:gluetun", "navn": "Start VPN-en på nytt", "auto": True}
    if group == "Prowlarr" and "Feiler" in text:
        return {"id": "prowlarr:test", "navn": "Test indekserne igjen", "auto": False}
    if group == "Disk" and "ledig" in text:
        return {"id": "vis:disk", "navn": "Se diskinnstillinger", "auto": False}
    if "autoconfig" in (advice or "").lower():
        return {"id": "autoconfig", "navn": "Kjør autooppsettet", "auto": False}
    return None


def run_checks(reason="planlagt"):
    if not CHECK_LOCK.acquire(blocking=False):
        return
    try:
        ST["checking"] = True
        SJ.FREE_WARN_GB = float(SETTINGS["disk"]["varsel_gb"])
        SJ.log = lambda *a, **k: None            # sjekken skriver ellers til containerloggen
        SJ.ROWS.clear()
        for name, fn in SJ.plan():
            SJ.section(name, fn)
        rows = []
        for i, (g, lvl, text, advice) in enumerate(SJ.ROWS):
            fix = find_fix(g, lvl, text, advice)
            lenke = advice if (advice or "").startswith("http") else ""
            rows.append({"id": f"{g}:{i}", "gruppe": g, "niva": NIVA[lvl], "tekst": text,
                         "rad": "" if lenke else advice, "lenke": lenke, "fiks": fix})
        with LOCK:
            ST["rows"], ST["checked"] = rows, time.time()
        write(f"{CFG}/SJEKK.txt", "\n".join(f"{SJ.SYMBOL[s]} {g}: {t}" + (f"  → {f}" if f else "")
                                           for g, s, t, f in SJ.ROWS) + f"\n\nKontrollpanelet, {time.strftime('%Y-%m-%d %H:%M')} ({reason})\n")
    finally:
        ST["checking"] = False
        CHECK_LOCK.release()
    auto_fix()


def auto_fix():
    if not SETTINGS.get("automatikk", True) or time.time() - STARTED < GRACE:
        return
    did = False
    for r in list(ST["rows"]):
        f = r.get("fiks")
        if not f or not f.get("auto") or r["niva"] == "grønn":
            continue
        now = time.time()
        tries = [t for t in ST["attempts"].get(f["id"], []) if now - t < AUTO_WINDOW]
        if len(tries) >= AUTO_MAX:
            f["forklaring"] = f"Prøvd automatisk {len(tries)} ganger uten å hjelpe. Trenger deg."
            f["auto"] = False
            continue
        ST["attempts"][f["id"]] = tries + [now]
        ok, msg = run_fix(f["id"], auto=True)
        event(f"Automatisk: {f['navn']} ({r['tekst']}) – {msg}", "auto")
        did = True
    if did:
        threading.Timer(90, run_checks, kwargs={"reason": "etter automatisk løsning"}).start()


def run_fix(fid, auto=False):
    """Kjører én løsning. Returnerer (ok, melding)."""
    kind, _, arg = fid.partition(":")
    if kind == "restart":
        return (docker_action(arg, "restart"), "startet på nytt")
    if kind == "stop":
        ok = docker_action(arg, "stop")
        if ok:
            ST["blocked"][arg] = "VPN-lekkasje – startes ikke automatisk før VPN-en virker"
        return ok, "stoppet for å hindre lekkasje"
    if kind == "start":
        ST["blocked"].pop(arg, None)
        return docker_action(arg, "start"), "startet"
    if kind == "compose" and arg == "up":
        ok, msg = compose_run("docker compose up -d", "start")
        return ok, msg
    if kind == "autoconfig":
        return start_autoconfig()
    if kind == "prowlarr" and arg == "test":
        p = SJ.arr("prowlarr", 9696, "v1")
        try:
            p.post("indexer/testall", {})
            return True, "indekserne er testet"
        except (RuntimeError, AttributeError) as e:
            return False, str(e)[:120]
    return False, "ukjent løsning"


def start_autoconfig():
    c = compose_containers("autoconfig")
    if c:
        s, _ = docker("POST", f"/containers/{c[0]['Id']}/start", timeout=30)
        if s in (204, 304):
            return True, "autooppsettet kjører (følg med i loggen: docker compose logs -f autoconfig)"
    return compose_run("docker compose up -d autoconfig", "autooppsett")


# =========================================================================== docker compose fra panelet
def compose_containers(service=None):
    labels = [f"com.docker.compose.project={PROJECT}"] + ([f"com.docker.compose.service={service}"] if service else [])
    s, b = docker("GET", "/containers/json?all=1&filters=" + urllib.parse.quote(json.dumps({"label": labels})))
    return json.loads(b) if s == 200 else []


def project_dir():
    """Stackens mappe på maskinen, slik Docker Compose oppga den da containeren ble laget."""
    s, b = docker("GET", f"/containers/{socket.gethostname()}/json")
    if s != 200:
        own = compose_containers("throttle")
        if not own:
            return ""
        s, b = docker("GET", f"/containers/{own[0]['Id']}/json")
        if s != 200:
            return ""
    return (json.loads(b).get("Config", {}).get("Labels") or {}).get("com.docker.compose.project.working_dir", "")


def compose_supported():
    wd = project_dir()
    if not wd:
        return False, "fant ikke stackens mappe"
    if re.match(r"^[A-Za-z]:[\\/]", wd):
        return False, "virker ikke på Windows – bruk Oppdater.bat"
    return True, wd


def compose_run(cmd, label, wait=True):
    """Kjører docker compose i en hjelpecontainer (docker:cli) med stackens mappe montert."""
    ok, wd = compose_supported()
    if not ok:
        return False, wd
    docker("DELETE", f"/containers/{RUNNER}?force=1")
    s, b = docker("POST", "/images/create?fromImage=docker&tag=cli", timeout=600)
    if s != 200:
        return False, f"fikk ikke lastet ned docker:cli ({s})"
    body = {"Image": "docker:cli", "Cmd": ["sh", "-c", cmd], "WorkingDir": wd,
            "Labels": {"film-stack.panel": label},
            "HostConfig": {"Binds": [f"{wd}:{wd}", "/var/run/docker.sock:/var/run/docker.sock"]}}
    s, b = docker("POST", f"/containers/create?name={RUNNER}", body)
    if s != 201:
        return False, f"kunne ikke lage hjelpecontainer ({s}: {b[:120]!r})"
    docker("POST", f"/containers/{RUNNER}/start")
    if not wait:
        return True, "startet"
    s, b = docker("POST", f"/containers/{RUNNER}/wait", timeout=3600)
    code = json.loads(b).get("StatusCode") if s == 200 else None
    out = docker_logs(RUNNER, 300)
    docker("DELETE", f"/containers/{RUNNER}?force=1")
    return code == 0, (out.strip().splitlines() or ["ferdig"])[-1][:160] if code == 0 else f"feilet: {out.strip()[-300:]}"


def finish_leftover_runner():
    """Panelet kan ha blitt startet på nytt av sin egen oppdatering. Hent resultatet fra hjelpecontaineren."""
    s, b = docker("GET", f"/containers/{RUNNER}/json")
    if s != 200:
        return
    c = json.loads(b)
    if c.get("State", {}).get("Running"):
        return
    code = c.get("State", {}).get("ExitCode")
    out = docker_logs(RUNNER, 300)
    docker("DELETE", f"/containers/{RUNNER}?force=1")
    if (c.get("Config", {}).get("Labels") or {}).get("film-stack.panel") == "oppdatering":
        u = ST["oppdatering"]
        u.update(kjorer=False, resultat="ok" if code == 0 else "feil", logg=out[-4000:])
        event("Oppdateringen er ferdig" if code == 0 else "Oppdateringen feilet – se Oppdateringer", "system")


# =========================================================================== oppdateringer
def someone_watching():
    try:
        if A.MEDIA == "jellyfin":
            r = A.jf("POST", "/Users/AuthenticateByName", body={"Username": A.USER, "Pw": A.PASSWORD})
            tok = (r.json() or {}).get("AccessToken") if r.ok else None
            sess = A.jf("GET", "/Sessions?activeWithinSeconds=120", tok).json() or [] if tok else []
            return any(x.get("NowPlayingItem") for x in sess)
        tok = plex_pref("PlexOnlineToken")
        r = http("GET", f"{A.PLEX_URL}/status/sessions", headers={"X-Plex-Token": tok, "Accept": "application/json"})
        return int(((r.json() or {}).get("MediaContainer") or {}).get("size", 0)) > 0 if r.ok else False
    except Exception:
        return False


def run_update(manual=False):
    u = ST["oppdatering"]
    if u.get("kjorer"):
        return False, "oppdateringen kjører allerede"
    ok, why = compose_supported()
    if not ok:
        return False, why
    u.update(kjorer=True, startet=time.time())
    save()
    event("Oppdaterer appene (henter nye versjoner)" + (" – startet for hånd" if manual else ""), "manuell" if manual else "system")
    cmd = ("docker compose pull --quiet --ignore-pull-failures && "
           "docker compose up -d --remove-orphans && docker image prune -f")
    ok, msg = compose_run(cmd, "oppdatering")
    u.update(kjorer=False, siste=time.time(), resultat="ok" if ok else "feil", logg=msg)
    event("Oppdateringen er ferdig" if ok else f"Oppdateringen feilet: {msg[:160]}", "system")
    threading.Timer(180, run_checks, kwargs={"reason": "etter oppdatering"}).start()
    return ok, msg


def update_due(now=None):
    o = SETTINGS["oppdatering"]
    if o["modus"] == "av":
        return False
    now = now or datetime.now()
    h, m = (int(x) for x in o["klokke"].split(":"))
    if (now.hour, now.minute) < (h, m) or (now.hour, now.minute) >= (h + 3, m):   # tre timers vindu
        return False
    if o["modus"] == "ukentlig" and now.weekday() != int(o["dag"]):
        return False
    last = datetime.fromtimestamp(ST["oppdatering"].get("siste") or 0)
    return last.date() != now.date()


def next_update_text():
    o = SETTINGS["oppdatering"]
    if o["modus"] == "av":
        return "Automatiske oppdateringer er av"
    when = f"hver natt kl. {o['klokke']}" if o["modus"] == "daglig" else f"hver {DAGER[int(o['dag'])]} kl. {o['klokke']}"
    return f"Oppdateres {when}" + (", men venter hvis noen ser på noe" if o.get("vent_paa_avspilling") else "")


# =========================================================================== fart og disk
def qbit(method, path, form=None):
    return http(method, f"{A.QBIT}/api/v2/{path}", form=form, headers={"Referer": A.QBIT}, timeout=15)


def nzb(method, params=()):
    import base64
    auth = base64.b64encode(f"{A.USER}:{A.PASSWORD}".encode()).decode()
    r = http("POST", "http://nzbget:6789/jsonrpc", headers={"Authorization": f"Basic {auth}"},
             json_body={"method": method, "params": list(params), "id": 1}, timeout=15)
    return r.json() if r.ok else None


def _hm(s):
    h, m = (int(x) for x in s.split(":"))
    return h, m


def in_calm_window(now=None):
    f = SETTINGS["fart"]
    if not f.get("plan"):
        return False
    now = now or datetime.now()
    a, b, cur = _hm(f["plan_fra"]), _hm(f["plan_til"]), (now.hour, now.minute)
    return a <= cur < b if a <= b else cur >= a or cur < b


def apply_speed():
    """Legger fartsgrensene inn i qBittorrent (inkl. tidsplan) og NZBGet. MB/s, 0 = ubegrenset."""
    f = SETTINGS["fart"]
    mb = lambda v: int(float(v or 0) * 1024 * 1024)  # noqa: E731
    prefs = {"dl_limit": mb(f["ned_mb"]), "up_limit": mb(f["opp_mb"]),
             "alt_dl_limit": mb(f["rolig_ned_mb"]), "alt_up_limit": mb(f["rolig_opp_mb"]),
             "scheduler_enabled": bool(f.get("plan"))}
    if f.get("plan"):
        (fh, fm), (th, tm) = _hm(f["plan_fra"]), _hm(f["plan_til"])
        prefs.update(schedule_from_hour=fh, schedule_from_min=fm, schedule_to_hour=th, schedule_to_min=tm,
                     scheduler_days=0)
    r = qbit("POST", "app/setPreferences", {"json": json.dumps(prefs)})
    res = {"qBittorrent": r.ok}
    if A.USENET:
        res["NZBGet"] = apply_nzb_rate()
    return res


def apply_nzb_rate(calm=None):
    f = SETTINGS["fart"]
    if calm is None:
        calm = in_calm_window()
    kb = int(float(f["rolig_ned_mb"] if calm else f["ned_mb"]) * 1024)
    return bool(nzb("rate", [kb]))


def disk_info():
    out = {}
    try:
        du = shutil.disk_usage(DATA)
        out["data"] = {"total_gb": round(du.total / 1e9), "ledig_gb": round(du.free / 1e9),
                       "brukt_prosent": round(100 * (du.total - du.free) / du.total) if du.total else 0}
    except OSError as e:
        out["data"] = {"feil": str(e)}
    try:
        du = shutil.disk_usage(CFG)
        out["config"] = {"total_gb": round(du.total / 1e9), "ledig_gb": round(du.free / 1e9)}
    except OSError:
        pass
    best = ("", "", "")
    try:
        for line in open("/proc/mounts"):
            dev, mnt, fs = line.split()[:3]
            if (DATA == mnt or DATA.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best[1]):
                best = (dev, mnt, fs)
    except OSError:
        pass
    fs = best[2]
    out["type"] = ("NFS-deling" if fs.startswith("nfs") else "SMB-deling" if fs in ("cifs", "smb3") else
                   f"lokal disk ({fs})" if fs and fs not in ("overlay", "fakeowner") else "lokal mappe")
    out["kilde"] = best[0] if fs.startswith(("nfs", "cifs", "smb")) else ""
    out["pauset"] = ST["disk_pause"]
    return out


def disk_guard():
    """Pauser nedlastinger når disken nesten er full, og fortsetter når det er plass igjen."""
    try:
        free = shutil.disk_usage(DATA).free / 1e9
    except OSError:
        return
    limit = float(SETTINGS["disk"]["pause_gb"])
    if not ST["disk_pause"] and free < limit:
        r = qbit("POST", "torrents/stop", {"hashes": "all"})
        if r.status == 404:
            qbit("POST", "torrents/pause", {"hashes": "all"})
        if A.USENET:
            nzb("pausedownload")
        ST["disk_pause"] = True
        event(f"Pauset alle nedlastinger: bare {free:.0f} GB ledig (grensen er {limit:.0f} GB)", "auto")
    elif ST["disk_pause"] and free > limit + 5:
        r = qbit("POST", "torrents/start", {"hashes": "all"})
        if r.status == 404:
            qbit("POST", "torrents/resume", {"hashes": "all"})
        if A.USENET:
            nzb("resumedownload")
        ST["disk_pause"] = False
        event(f"Nedlastingene fortsetter: {free:.0f} GB ledig igjen", "auto")


# =========================================================================== indeksere (Prowlarr)
def indexers():
    p = SJ.arr("prowlarr", 9696, "v1")
    if not p:
        return {"feil": "fant ikke API-nøkkelen til Prowlarr"}
    try:
        ix = p.get("indexer")
    except RuntimeError as e:
        return {"feil": f"Prowlarr svarer ikke ({str(e)[:80]})"}
    try:
        status = {s["indexerId"]: s for s in p.get("indexerstatus")}
    except RuntimeError:
        status = {}
    try:
        stats = {s["indexerId"]: s for s in (p.get("indexerstats") or {}).get("indexers", [])}
    except RuntimeError:
        stats = {}
    out = []
    now = datetime.now().astimezone()
    for i in sorted(ix, key=lambda x: (x.get("priority", 25), x.get("name", ""))):
        st, sx = status.get(i["id"], {}), stats.get(i["id"], {})
        until = st.get("disabledTill")
        niva, tekst = "grønn", "Svarer"
        if not i.get("enable"):
            niva, tekst = "grå", "Slått av"
        elif until and datetime.fromisoformat(until.replace("Z", "+00:00")) > now:
            t = datetime.fromisoformat(until.replace("Z", "+00:00")).astimezone()
            when = f"kl. {t:%H:%M}" if t.date() == now.date() else f"{t:%d.%m.} kl. {t:%H:%M}"
            niva, tekst = "rød", f"Satt på pause av Prowlarr til {when} etter feil"
        elif st.get("mostRecentFailure"):
            niva, tekst = "gul", "Har feilet nylig, men svarer nå"
        q, fails = sx.get("numberOfQueries", 0), sx.get("numberOfFailedQueries", 0)
        out.append({"id": i["id"], "navn": i.get("name"), "type": i.get("protocol"), "pa": bool(i.get("enable")),
                    "niva": niva, "tekst": tekst, "sok": q, "feil": fails, "hentet": sx.get("numberOfGrabs", 0),
                    "svartid_ms": sx.get("averageResponseTime"), "privat": i.get("privacy")})
    return {"liste": out}


def indexer_action(iid, action):
    p = SJ.arr("prowlarr", 9696, "v1")
    if not p:
        return False, "fant ikke Prowlarr"
    try:
        ix = p.get(f"indexer/{iid}")
        if action == "test":
            p.post("indexer/test", ix)
            return True, f"{ix['name']} svarer"
        if action in ("pa", "av"):
            ix["enable"] = action == "pa"
            p.put(f"indexer/{iid}", ix)
            event(f"Indekseren {ix['name']} er slått {'på' if ix['enable'] else 'av'}", "manuell")
            return True, "lagret"
    except RuntimeError as e:
        msg = str(e)
        m = re.search(r'"errorMessage"\s*:\s*"([^"]+)"', msg)
        return False, (m.group(1) if m else msg)[:200]
    return False, "ukjent handling"


# =========================================================================== debug-logging
def _arr_level(app, port, ver, level):
    key = xml_apikey(f"{CFG}/{app}/config.xml")
    if not key:
        return False
    a = A.Arr(app, f"http://{app}:{port}", key, ver)
    try:
        h = a.get("config/host")
        h["logLevel"] = level
        a.put_config("host", h)
        return True
    except RuntimeError:
        return False


def _jellyfin_debug(on):
    hits = glob.glob(f"{CFG}/jellyfin/logging.default.json") + glob.glob(f"{CFG}/jellyfin/*/logging.default.json")
    if not hits:
        return False
    target = os.path.join(os.path.dirname(hits[0]), "logging.json")
    if on:
        write(target, json.dumps({"FilmStack": "debug slått på fra kontrollpanelet – slettes når det slås av",
                                  "Serilog": {"MinimumLevel": {"Default": "Debug"}}}, indent=2),
              owner=(A.PUID, A.PGID))
    elif os.path.exists(target) and '"FilmStack"' in read(target):
        os.remove(target)
    return True


def _bazarr_debug(on):
    m = re.search(r"^auth:\s*\n(?:[ \t]+.*\n)*?[ \t]+apikey:\s*['\"]?([A-Za-z0-9]+)",
                  read(f"{CFG}/bazarr/config/config.yaml"), re.M)
    if not m:
        return False
    r = http("POST", "http://bazarr:6767/api/system/settings", headers={"X-API-KEY": m.group(1)},
             form={"settings-general-debug": "true" if on else "false"})
    return r.ok


def _plex_debug(on):
    tok = plex_pref("PlexOnlineToken")
    if not tok:
        return False
    r = http("PUT", f"{A.PLEX_URL}/:/prefs?logDebug={1 if on else 0}", headers={"X-Plex-Token": tok})
    return r.ok


def set_debug(on):
    level = "debug" if on else "info"
    res = {"Sonarr": _arr_level("sonarr", 8989, "v3", level),
           "Radarr": _arr_level("radarr", 7878, "v3", level),
           "Prowlarr": _arr_level("prowlarr", 9696, "v1", level),
           "Bazarr": _bazarr_debug(on)}
    if A.MEDIA == "jellyfin":
        res["Jellyfin"] = _jellyfin_debug(on)
    else:
        res["Plex"] = _plex_debug(on)
    ST["debug_til"] = time.time() + DEBUG_HOURS * 3600 if on else 0
    ST["debug_resultat"] = res
    okn = [k for k, v in res.items() if v]
    bad = [k for k, v in res.items() if not v]
    event(f"Debug-logging {'på i ' + str(DEBUG_HOURS) + ' timer' if on else 'av'}: {', '.join(okn) or 'ingen'}"
          + (f" (fikk ikke til: {', '.join(bad)})" if bad else ""), "manuell")
    return res


# =========================================================================== loggpakke
PRIVATE = re.compile(r"^(10\.|127\.|0\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)")
KV = re.compile(r"""(?i)((?:api[_-]?key|apikey|x-api-key|access[_-]?token|refresh[_-]?token|token|password|passwd|"""
                r"""secret|authorization|private[_-]?key|privatekey|cookie|session[_-]?id|sid)["']?\s*[:=]\s*["']?"""
                r"""(?:Basic |Bearer )?)([^\s"'&,;}<>]{3,})""")
HEX = re.compile(r"\b[a-fA-F0-9]{32,}\b")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


class Redactor:
    """Fjerner passord, nøkler, token, e-postadresser og offentlige IP-adresser fra loggene."""

    def __init__(self, secrets):
        self.secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)
        self.ips = {}

    def _ip(self, m):
        ip = m.group(1)
        parts = ip.split(".")
        if any(int(p) > 255 for p in parts) or PRIVATE.match(ip):
            return ip
        self.ips.setdefault(ip, f"offentlig-IP-{len(self.ips) + 1}")
        return self.ips[ip]

    def __call__(self, text):
        for s in self.secrets:
            text = text.replace(s, "[SLADDET]")
        text = KV.sub(lambda m: m.group(1) + "[SLADDET]", text)
        text = JWT.sub("[TOKEN]", text)
        text = HEX.sub("[NØKKEL]", text)
        text = EMAIL.sub("[E-POST]", text)
        return IPV4.sub(self._ip, text)


def collect_secrets():
    s = [env(k) for k in ("ADMIN_PASSWORD", "USENET_PASSWORD", "PLEX_ACCOUNT_TOKEN", "WIREGUARD_PRIVATE_KEY",
                          "OPENVPN_PASSWORD")]
    s += [xml_apikey(f"{CFG}/{a}/config.xml") for a in ("sonarr", "radarr", "prowlarr")]
    m = re.search(r"apikey:\s*['\"]?([A-Za-z0-9]+)", read(f"{CFG}/bazarr/config/config.yaml"))
    s.append(m.group(1) if m else "")
    try:
        s.append((json.loads(read(f"{CFG}/seerr/settings.json") or "{}").get("main") or {}).get("apiKey", ""))
    except ValueError:
        pass
    s.append(plex_pref("PlexOnlineToken") or "")
    return s


def _tail(path, limit=3_000_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", errors="replace")
    except OSError as e:
        return f"(kunne ikke lese: {e})"


def _newest(pattern, n):
    files = [f for f in glob.glob(pattern, recursive=True) if os.path.isfile(f)]
    return sorted(files, key=os.path.getmtime, reverse=True)[:n]


APP_LOGS = [
    ("sonarr", f"{CFG}/sonarr/logs/*.txt", 3), ("radarr", f"{CFG}/radarr/logs/*.txt", 3),
    ("prowlarr", f"{CFG}/prowlarr/logs/*.txt", 3), ("bazarr", f"{CFG}/bazarr/log/bazarr.log*", 2),
    ("jellyfin", f"{CFG}/jellyfin/**/log/*.log", 3),
    ("plex", f"{CFG}/plex/Library/Application Support/Plex Media Server/Logs/Plex Media Server*.log", 2),
    ("seerr", f"{CFG}/seerr/logs/*.log", 2), ("qbittorrent", f"{CFG}/qbittorrent/**/logs/qbittorrent.log", 1),
    ("nzbget", f"{CFG}/nzbget/*.log", 1), ("recyclarr", f"{CFG}/recyclarr/logs/**/*.log", 2),
]


def logs_zip():
    red = Redactor(collect_secrets())
    buf = io.BytesIO()
    stamp = time.strftime("%Y-%m-%d %H:%M")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        def put(name, text):
            z.writestr(name, red(text if isinstance(text, str) else json.dumps(text, indent=2, ensure_ascii=False)))
        version = read("/stack/VERSION").strip() or read(os.path.join(os.path.dirname(__file__), "..", "VERSION")).strip()
        put("LES-MEG.txt",
            f"Loggpakke fra Film-stack {version}, laget {stamp}.\n\n"
            "Passord, API-nøkler, token, e-postadresser og offentlige IP-adresser er byttet ut med\n"
            "[SLADDET], [NØKKEL], [TOKEN], [E-POST] og offentlig-IP-N. Se likevel gjennom før du deler.\n"
            f"Debug-logging: {'på' if ST['debug_til'] > time.time() else 'av'}.\n\n"
            "status.json      – siste sjekk, innstillinger og hendelser fra kontrollpanelet\n"
            "containere.json  – alle tjenestene, tilstand, omstarter og versjoner\n"
            "system.txt       – Docker, disk og maskin\n"
            "docker/          – det hver container har skrevet (siste 4000 linjer)\n"
            "apper/           – appenes egne loggfiler (de nyeste)\n")
        put("status.json", {"sjekket": ST["checked"], "funn": ST["rows"], "innstillinger": SETTINGS,
                            "hendelser": ST["events"][:100], "oppdatering": ST["oppdatering"],
                            "debug": ST["debug_resultat"], "disk": disk_info()})
        cs = []
        for c in compose_containers():
            s, b = docker("GET", f"/containers/{c['Id']}/json")
            ins = json.loads(b) if s == 200 else {}
            name = (c.get("Names") or ["/?"])[0].lstrip("/")
            cs.append({"navn": name, "tjeneste": (c.get("Labels") or {}).get("com.docker.compose.service"),
                       "image": c.get("Image"), "tilstand": c.get("State"), "status": c.get("Status"),
                       "omstarter": ins.get("RestartCount"), "startet": (ins.get("State") or {}).get("StartedAt"),
                       "helse": ((ins.get("State") or {}).get("Health") or {}).get("Status"),
                       "prosessorgrense": (ins.get("HostConfig") or {}).get("NanoCpus")})
            s, lb = docker("GET", f"/containers/{c['Id']}/logs?stdout=1&stderr=1&timestamps=1&tail=4000")
            if s == 200:
                put(f"docker/{name}.log", demux(lb).decode("utf-8", errors="replace"))
        put("containere.json", cs)
        s, b = docker("GET", "/info")
        info = json.loads(b) if s == 200 else {}
        sysinfo = [f"{k}: {info.get(k)}" for k in ("ServerVersion", "OperatingSystem", "KernelVersion", "Architecture",
                                                     "NCPU", "MemTotal", "Driver", "DockerRootDir")]
        sysinfo += ["", f"Disk: {json.dumps(disk_info(), ensure_ascii=False)}"]
        try:
            sysinfo += ["", "Monteringer for /data og /config:"] + [
                l.strip() for l in open("/proc/mounts") if " /data" in l or " /config" in l]
            sysinfo += ["", "Oppetid: " + open("/proc/uptime").read().split()[0] + " s",
                        "Belastning: " + open("/proc/loadavg").read().strip()]
        except OSError:
            pass
        put("system.txt", "\n".join(sysinfo))
        keys = ["MEDIA_SERVER", "COMPOSE_PROFILES", "QBIT_HOST", "HWACCEL", "MOVIES_DIR", "TV_DIR", "SUBTITLE_LANGS",
                "MIN_RESOLUTION", "WHISPER", "WHISPER_MODEL", "WHISPER_AUTO", "USENET_HOST", "USENET_CONNECTIONS",
                "TZ", "PUID", "PGID", "LAN_IP", "DASHBOARD_HOST"]
        put("innstillinger-env.txt", "\n".join(f"{k}={env(k)}" for k in keys) +
            f"\nADMIN_PASSWORD={'(satt)' if env('ADMIN_PASSWORD') else '(tom)'}\n")
        for app, pattern, n in APP_LOGS:
            for f in _newest(pattern, n):
                put(f"apper/{app}/{os.path.basename(f)}", _tail(f))
        sj = f"{CFG}/SJEKK.txt"
        if os.path.exists(sj):
            put("SJEKK.txt", read(sj))
    return buf.getvalue()


# =========================================================================== bakgrunn
def loop(is_calm=lambda: False):
    load()
    finish_leftover_runner()
    time.sleep(20)
    try:
        apply_speed()
    except Exception as e:
        log(f"panel: fartsgrenser: {e}")
    last_check = 0
    last_calm = None
    while True:
        try:
            now = time.time()
            if now - last_check > CHECK_EVERY:
                last_check = now
                run_checks()
            if ST["debug_til"] and now > ST["debug_til"]:
                set_debug(False)
                event("Debug-logging slått av igjen etter 24 timer", "auto")
            disk_guard()
            calm = is_calm() or in_calm_window()
            if A.USENET and calm != last_calm:
                apply_nzb_rate(calm)
                last_calm = calm
            u = ST["oppdatering"]
            if update_due() and not u.get("kjorer") and now >= u.get("utsatt_til", 0):
                if SETTINGS["oppdatering"].get("vent_paa_avspilling") and someone_watching():
                    u["utsatt_til"] = now + 1800
                    event("Oppdateringen venter 30 minutter: noen ser på noe", "system")
                else:
                    threading.Thread(target=run_update, daemon=True).start()
        except Exception as e:
            log(f"panel: {e}")
        time.sleep(60)


def summary():
    rows = ST["rows"]
    red = sum(r["niva"] == "rød" for r in rows)
    yellow = sum(r["niva"] == "gul" for r in rows)
    if not rows:
        return "Sjekker …"
    if red:
        return f"{red} feil" + (f", {yellow} å se på" if yellow else "")
    return f"{yellow} å se på" if yellow else "Alt virker"


def state(cooling):
    ok, why = compose_supported()
    return {
        "stack": PROJECT, "media": A.MEDIA, "versjon": read("/stack/VERSION").strip(),
        "oppsummering": summary(), "sjekket": ST["checked"], "sjekker": ST["checking"],
        "funn": ST["rows"], "hendelser": ST["events"][:60], "innstillinger": SETTINGS,
        "disk": disk_info(), "kjoling": cooling,
        "oppdatering": {**ST["oppdatering"], "plan": next_update_text(), "mulig": ok, "hvorfor": "" if ok else why},
        "debug": {"pa": ST["debug_til"] > time.time(), "til": ST["debug_til"], "resultat": ST["debug_resultat"]},
        "usenet": A.USENET, "kvalitet": ST["kvalitet"],
        "lenker": {"prowlarr": f"http://{A.DASH}:9696", "qbittorrent": f"http://{A.DASH}:8080",
                   "sonarr": f"http://{A.DASH}:8989", "radarr": f"http://{A.DASH}:7878",
                   "dashboard": f"http://{A.DASH}:3000"},
    }


# =========================================================================== innstillinger
def _num(v, lo, hi):
    v = float(v)
    if not lo <= v <= hi:
        raise ValueError(f"{v} er utenfor {lo}–{hi}")
    return int(v) if v == int(v) else v


def _clock(v):
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(v)):
        raise ValueError(f"«{v}» er ikke et klokkeslett (TT:MM)")
    return str(v)


def update_settings(section, values):
    """Validerer, lagrer og legger inn innstillingene. Returnerer (ok, melding)."""
    try:
        if section == "kvalitet":
            res = int(values["min_opplosning"])
            if res not in (720, 1080, 2160):
                raise ValueError("velg 720, 1080 eller 2160")
            SETTINGS["kvalitet"]["min_opplosning"] = res
            save()
            summ = quality.enforce(res, verbose=False)
            ST["kvalitet"] = {"tid": time.time(), "apper": {a: {"strammet": s[0], "fjernet": s[1], "flyttet": s[2],
                                                                 "hovedprofil": s[3]} for a, s in summ.items()}}
            event(f"Kvalitet: bare {res}p eller bedre", "manuell")
            return True, f"Lagret. Sonarr og Radarr henter nå bare {res}p eller bedre."
        if section == "disk":
            SETTINGS["disk"].update(varsel_gb=_num(values["varsel_gb"], 1, 100000),
                                    pause_gb=_num(values["pause_gb"], 1, 100000))
            SJ.FREE_WARN_GB = float(SETTINGS["disk"]["varsel_gb"])
            save()
            event(f"Disk: varsel under {SETTINGS['disk']['varsel_gb']} GB, pause under {SETTINGS['disk']['pause_gb']} GB", "manuell")
            return True, "Lagret."
        if section == "fart":
            f = SETTINGS["fart"]
            for k in ("ned_mb", "opp_mb", "rolig_ned_mb", "rolig_opp_mb"):
                f[k] = _num(values.get(k, f[k]), 0, 10000)
            f["plan"] = bool(values.get("plan"))
            f["plan_fra"], f["plan_til"] = _clock(values.get("plan_fra", f["plan_fra"])), _clock(values.get("plan_til", f["plan_til"]))
            save()
            res = apply_speed()
            bad = [k for k, v in res.items() if not v]
            event("Fartsgrenser oppdatert" + (f" (fikk ikke kontakt med {', '.join(bad)})" if bad else ""), "manuell")
            return not bad, "Lagret og lagt inn." if not bad else f"Lagret, men {', '.join(bad)} svarte ikke. Prøves igjen ved neste start."
        if section == "oppdatering":
            o = SETTINGS["oppdatering"]
            if values.get("modus") not in ("av", "daglig", "ukentlig"):
                raise ValueError("ukjent modus")
            o.update(modus=values["modus"], dag=int(_num(values.get("dag", o["dag"]), 0, 6)),
                     klokke=_clock(values.get("klokke", o["klokke"])),
                     vent_paa_avspilling=bool(values.get("vent_paa_avspilling")))
            save()
            event(next_update_text(), "manuell")
            return True, next_update_text() + "."
        if section == "automatikk":
            SETTINGS["automatikk"] = bool(values.get("pa"))
            save()
            event("Trygge løsninger kjøres " + ("automatisk" if SETTINGS["automatikk"] else "bare når du trykker"), "manuell")
            return True, "Lagret."
    except (KeyError, ValueError, TypeError) as e:
        return False, f"Ugyldig verdi: {e}"
    return False, "ukjent innstilling"


# =========================================================================== nettside og API
PAGE_FILE = os.path.join(os.path.dirname(__file__), "panel.html")


def _authorized(h):
    if not A.PASSWORD:
        return True
    import base64
    want = "Basic " + base64.b64encode(f"{A.USER}:{A.PASSWORD}".encode()).decode()
    return h.headers.get("Authorization", "") == want


def _send(h, code, body, ctype="application/json; charset=utf-8", extra=None):
    b = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
    h.send_response(code)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(b)))
    h.send_header("Cache-Control", "no-store")
    for k, v in (extra or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(b)


def handle(h, method, cooling, set_cooling):
    """Svarer på alt panelet eier. Returnerer False for adresser det ikke kjenner."""
    path = urllib.parse.urlparse(h.path).path
    if not _authorized(h):
        _send(h, 401, "Logg inn med brukernavnet og passordet fra veiviseren.", "text/plain; charset=utf-8",
              {"WWW-Authenticate": 'Basic realm="Film-stack kontrollpanel", charset="UTF-8"'})
        return True
    body = {}
    if method == "POST":
        n = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(n).decode() if n else ""
        try:
            body = json.loads(raw) if raw.strip().startswith("{") else {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        except ValueError:
            body = {}
    if method == "GET" and path in ("/", "/index.html"):
        _send(h, 200, read(PAGE_FILE), "text/html; charset=utf-8")
    elif method == "GET" and path == "/api/panel":
        _send(h, 200, state(cooling()))
    elif method == "GET" and path == "/api/indeksere":
        _send(h, 200, indexers())
    elif method == "GET" and path == "/api/logger.zip":
        data = logs_zip()
        _send(h, 200, data, "application/zip",
              {"Content-Disposition": f'attachment; filename="{PROJECT}-logger-{time.strftime("%Y%m%d-%H%M")}.zip"'})
    elif method == "POST" and path == "/api/sjekk":
        threading.Thread(target=run_checks, kwargs={"reason": "startet for hånd"}, daemon=True).start()
        _send(h, 202, {"ok": True, "melding": "Sjekker nå. Tar et halvt til et par minutter."})
    elif method == "POST" and path == "/api/fiks":
        fid = str(body.get("id", ""))
        ok, msg = run_fix(fid)
        event(f"{'Fikset' if ok else 'Prøvde å fikse'}: {fid} – {msg}", "manuell")
        threading.Timer(60, run_checks, kwargs={"reason": "etter løsning"}).start()
        _send(h, 200, {"ok": ok, "melding": msg})
    elif method == "POST" and path.startswith("/api/innstillinger/"):
        ok, msg = update_settings(path.rsplit("/", 1)[1], body)
        _send(h, 200 if ok else 400, {"ok": ok, "melding": msg})
    elif method == "POST" and path == "/api/oppdater":
        ok, why = compose_supported()
        if ok:
            threading.Thread(target=run_update, kwargs={"manual": True}, daemon=True).start()
        _send(h, 202 if ok else 400, {"ok": ok, "melding": "Oppdaterer. Panelet kan forsvinne et minutt mens det startes på nytt." if ok else why})
    elif method == "POST" and path == "/api/debug":
        res = set_debug(bool(body.get("pa")))
        _send(h, 200, {"ok": any(res.values()), "resultat": res})
    elif method == "POST" and path.startswith("/api/indeksere/"):
        parts = path.split("/")
        ok, msg = indexer_action(int(parts[3]), parts[4] if len(parts) > 4 else "")
        _send(h, 200 if ok else 400, {"ok": ok, "melding": msg})
    elif method == "POST" and path == "/api/kjoling":
        set_cooling(body.get("modus", ""))
        _send(h, 200, {"ok": True})
    else:
        return False
    return True
