"""Kjøling: måler maskinen og roer ned bakgrunnsappene når den blir varm eller travel.

Jellyfin og Plex begrenses ALDRI – avspilling og transkoding går som normalt.
I rolig modus:
  • bakgrunnsappene får en lav prosessorgrense (docker update, ingen omstart)
  • qBittorrent bytter til alternative (lave) fartsgrenser
  • NZBGet venter med utpakking/reparasjon
  • Plex stopper planlagte vedlikeholdsjobber (forhåndsbilder o.l.), hvis Plex brukes
Alt settes tilbake når maskinen har roet seg.

Kontrollpanelet (panel.py) bor i samme tjeneste: http://<server>:3010
Åpent for dashboardet: /api/status. Alt annet krever innloggingen fra veiviseren.
"""
import base64
import glob
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
import panel  # noqa: E402
import quality  # noqa: E402
from common import CFG, docker, env, http, log, plex_pref, read, write  # noqa: E402

BG = ["sonarr", "radarr", "qbittorrent", "nzbget", "bazarr", "prowlarr", "flaresolverr", "seerr",
      "tautulli", "recyclarr", "prefetcharr", "homepage", "autobrr", "wizarr", "whisper"]
HOT = float(env("HOT_TEMP", "85"))          # °C – roe ned over dette
COOL = float(env("COOL_TEMP", "72"))        # °C – tilbake til normal under dette
HIGH = float(env("HIGH_LOAD", "90"))        # % prosessor over tid – roe ned
LOW = float(env("LOW_LOAD", "50"))          # % prosessor over tid – tilbake til normal
CALM_CPUS = float(env("CALM_CPUS", "0.3"))  # prosessorgrense per bakgrunnsapp i rolig modus
HOT_SECS, COOL_SECS, TICK = 120, 300, 10
STATE_FILE = f"{CFG}/throttle/state.json"
QBIT = f"http://{env('QBIT_HOST', 'gluetun')}:8080"
PLEX_URL = env("PLEX_INTERNAL_URL", "http://plex:32400").rstrip("/")
USER, PASSWORD = env("ADMIN_USER", "admin"), env("ADMIN_PASSWORD")

LOCK = threading.Lock()
S = {"mode": "auto", "calm": False, "reason": "", "since": time.time(), "orig": {},
     "temp": None, "cpu": None, "mem": None, "history": []}


# --------------------------------------------------------------------------- målinger
def cpu_temp():
    vals = []
    for f in glob.glob("/sys/class/hwmon/hwmon*/temp*_input") + glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            v = int(open(f).read().strip()) / 1000
            if 5 < v < 130:
                vals.append(v)
        except (OSError, ValueError):
            pass
    return round(max(vals), 1) if vals else None


_last_cpu = None


def cpu_percent():
    """Hele maskinens prosessorbruk siden forrige måling (fra /proc/stat)."""
    global _last_cpu
    try:
        f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    except (OSError, ValueError):
        return None
    idle, total = f[3] + (f[4] if len(f) > 4 else 0), sum(f)
    prev, _last_cpu = _last_cpu, (idle, total)
    if not prev or total == prev[1]:
        return None
    return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def mem_percent():
    try:
        m = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        return round(100 * (1 - m["MemAvailable"] / m["MemTotal"]), 1)
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        return None


# --------------------------------------------------------------------------- handlinger
def set_cpus(name, nano):
    s, _ = docker("POST", f"/containers/{name}/update", {"NanoCpus": int(nano)})
    return s == 200


def container_nanocpus(name):
    s, b = docker("GET", f"/containers/{name}/json")
    if s != 200:
        return None
    c = json.loads(b)
    if not c.get("State", {}).get("Running"):
        return None
    return c.get("HostConfig", {}).get("NanoCpus", 0) or 0


def qbit_alt_speed(on):
    r = http("GET", f"{QBIT}/api/v2/transfer/speedLimitsMode", headers={"Referer": QBIT})
    if r.ok and r.text.strip() in ("0", "1") and (r.text.strip() == "1") != on:
        http("POST", f"{QBIT}/api/v2/transfer/toggleSpeedLimitsMode", headers={"Referer": QBIT})


def nzbget(method):
    auth = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    http("POST", "http://nzbget:6789/jsonrpc", headers={"Authorization": f"Basic {auth}"},
         json_body={"method": method, "params": [], "id": 1}, timeout=10)


def plex_stop_maintenance():
    if env("MEDIA_SERVER", "jellyfin") != "plex":
        return
    tok = plex_pref("PlexOnlineToken")
    if tok:
        http("DELETE", f"{PLEX_URL}/butler", headers={"X-Plex-Token": tok}, timeout=10)


def calm_on(reason):
    target = int(CALM_CPUS * 1e9)
    for name in BG:
        cur = container_nanocpus(name)
        if cur is None:
            continue
        S["orig"].setdefault(name, cur)
        orig = S["orig"][name]
        set_cpus(name, min(target, orig) if orig else target)
    qbit_alt_speed(True)
    nzbget("pausepost")
    plex_stop_maintenance()
    S.update(calm=True, reason=reason, since=time.time())
    log(f"{time.strftime('%H:%M')} ROLIG: {reason}")
    save()


def calm_off(reason):
    for name, orig in list(S["orig"].items()):
        set_cpus(name, orig)
    S["orig"] = {}
    qbit_alt_speed(False)
    nzbget("resumepost")
    S.update(calm=False, reason=reason, since=time.time())
    log(f"{time.strftime('%H:%M')} NORMAL: {reason}")
    save()


def save():
    write(STATE_FILE, json.dumps({k: S[k] for k in ("mode", "calm", "reason", "since", "orig")}))


def load():
    try:
        S.update(json.loads(read(STATE_FILE) or "{}"))
    except ValueError:
        pass


# --------------------------------------------------------------------------- regler
def sustained(hist, secs, test):
    now = time.time()
    window = [h for h in hist if now - h[0] <= secs]
    return bool(window) and now - window[0][0] >= secs * 0.8 and all(test(h) for h in window)


def decide():
    now = time.time()
    t, c, m = cpu_temp(), cpu_percent(), mem_percent()
    S.update(temp=t, cpu=c if c is not None else S["cpu"], mem=m)
    S["history"] = [h for h in S["history"] if now - h[0] <= COOL_SECS + 30] + [(now, t, S["cpu"])]
    hist = S["history"]
    if S["mode"] == "calm":
        if not S["calm"]:
            calm_on("slått på manuelt")
        return
    if S["mode"] == "normal":
        if S["calm"]:
            calm_off("slått av manuelt")
        return
    hot_temp = t is not None and t >= HOT
    busy = sustained(hist, HOT_SECS, lambda h: h[2] is not None and h[2] >= HIGH)
    cool = sustained(hist, COOL_SECS, lambda h: (h[1] is None or h[1] <= COOL) and (h[2] is None or h[2] <= LOW))
    if not S["calm"] and (hot_temp or busy):
        calm_on(f"{t:.0f} °C" if hot_temp else f"prosessoren over {HIGH:.0f} % i {HOT_SECS // 60} min")
    elif S["calm"] and cool:
        calm_off("maskinen har roet seg")


def status():
    mins = int((time.time() - S["since"]) // 60)
    return {
        "status": "Rolig" if S["calm"] else "Normal",
        "modus": {"auto": "Automatisk", "calm": "Alltid rolig", "normal": "Aldri rolig"}[S["mode"]],
        "temp": S["temp"], "cpu": S["cpu"], "mem": S["mem"],
        "grunn": S["reason"] or "–", "siden": f"{mins // 60} t {mins % 60} min" if mins >= 60 else f"{mins} min",
        "grenser": {"varm": HOT, "kjolig": COOL, "travel": HIGH},
    }


# --------------------------------------------------------------------------- nettside
def cooling():
    with LOCK:
        st = status()
    st["modus_id"] = S["mode"]
    st["grenser"].update(rolig_under=COOL, rolig_last=LOW, prosessorgrense=CALM_CPUS)
    return st


def set_mode(mode):
    if mode in ("auto", "calm", "normal"):
        with LOCK:
            S["mode"] = mode
            save()
            decide()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/status"):      # åpen: brukes av kortet i dashboardet
            st = cooling()
            st["helse"] = panel.summary()
            return self.send(200, json.dumps(st, ensure_ascii=False), "application/json; charset=utf-8")
        if not panel.handle(self, "GET", cooling, set_mode):
            self.send(404, "Finnes ikke", "text/plain; charset=utf-8")

    def do_POST(self):
        if not panel.handle(self, "POST", cooling, set_mode):
            self.send(404, "Finnes ikke", "text/plain; charset=utf-8")


def quality_guard():
    """Recyclarr setter guidens kvaliteter tilbake hver natt – stram inn igjen jevnlig."""
    while True:
        time.sleep(900)
        try:
            for app, (fixed, removed, moved, _) in quality.enforce(panel.minres(), verbose=False).items():
                if fixed or removed or moved:
                    log(f"{time.strftime('%H:%M')} kvalitetsvakt: {app} strammet inn igjen")
        except Exception as e:
            log(f"kvalitetsvakt: {e}")


def main():
    load()
    if S["calm"] and S["mode"] != "calm":    # startet på nytt mens den var rolig – rydd opp
        calm_off("kjøling startet på nytt")
    log(f"Kjøling startet. Rolig over {HOT:.0f} °C / {HIGH:.0f} % prosessor, normal under "
        f"{COOL:.0f} °C / {LOW:.0f} %. Temperatur nå: {cpu_temp()} °C. Kontrollpanel: http://<server>:3010")
    cpu_percent()
    srv = ThreadingHTTPServer(("0.0.0.0", 3010), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=quality_guard, daemon=True).start()
    threading.Thread(target=panel.loop, kwargs={"is_calm": lambda: S["calm"]}, daemon=True).start()
    while True:
        time.sleep(TICK)
        with LOCK:
            try:
                decide()
            except Exception as e:  # måling eller Docker-kall feilet – prøv igjen neste runde
                log(f"feil: {e}")


if __name__ == "__main__":
    main()
