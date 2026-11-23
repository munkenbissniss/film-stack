"""Sjekk: tester at hele installasjonen faktisk virker, fra VPN til mediaserver.

    docker compose run --rm sjekk

Endrer ingenting (bortsett fra en liten testfil som slettes igjen). Hver test gir
✔ (virker), ⚠ (virker delvis / bør sees på) eller ✘ (virker ikke), med et forslag
til hva du gjør. Resultatet lagres også i config/SJEKK.txt.
"""
import json
import os
import re
import shutil
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
import autoconfig as A  # noqa: E402  (gjenbruker innlogging, nøkler og adresser)
from common import (CFG, DATA, Session, docker, docker_exec, docker_logs, env,  # noqa: E402
                    http, log, read, write, xml_apikey)

ROWS = []          # (gruppe, status, tekst, råd)
FREE_WARN_GB = 50  # varsle når det er mindre ledig plass enn dette (kan endres i kontrollpanelet)
SYMBOL = {"ok": "✔", "warn": "⚠", "fail": "✘"}


def add(group, status, text, fix=""):
    ROWS.append((group, status, text, fix))
    log(f"  {SYMBOL[status]} {text}" + (f"\n      → {fix}" if fix and status != "ok" else ""))


def section(name, fn):
    log(f"\n{name}")
    try:
        fn()
    except Exception as e:      # en sjekk som krasjer skal ikke stoppe resten
        add(name, "warn", f"kunne ikke sjekke: {e}")


def arr(app, port, ver="v3"):
    key = xml_apikey(f"{CFG}/{app}/config.xml")
    return A.Arr(app, f"http://{app}:{port}", key, ver) if key else None


# =========================================================================== containere
CORE = ["sonarr", "radarr", "prowlarr", "bazarr", "seerr", "homepage", "qbittorrent", "recyclarr", "throttle"]


def check_containers():
    s, b = docker("GET", "/containers/json?all=1&filters=" + urllib.parse.quote(json.dumps(
        {"label": ["com.docker.compose.project=film-stack"]})))
    if s != 200:
        return add("Containere", "fail", "får ikke kontakt med Docker", "Kjør sjekken med «docker compose run --rm sjekk».")
    found = {}
    for c in json.loads(b):
        svc = (c.get("Labels") or {}).get("com.docker.compose.service", "")
        if svc in ("setup", "init", "autoconfig", "sjekk"):
            continue
        found[svc] = c
    expected = CORE + [A.MEDIA] + (["gluetun"] if "gluetun" in A.QBIT else []) + \
        (["nzbget"] if A.USENET else []) + (["tautulli"] if A.MEDIA == "plex" else []) + \
        [p for p in A.PROFILES if p in ("wizarr", "autobrr", "whisper")]
    for svc in expected:
        c = found.get(svc)
        if not c:
            add("Containere", "fail", f"{svc} finnes ikke", "Kjør «docker compose up -d».")
            continue
        state, status = c.get("State"), c.get("Status", "")
        name = (c.get("Names") or ["/" + svc])[0].lstrip("/")
        if state != "running":
            tail = " | ".join(docker_logs(name, 5).strip().splitlines()[-2:])[:160]
            add("Containere", "fail", f"{svc} kjører ikke ({status})", f"Se «docker logs {name}». Siste linjer: {tail}")
        elif "unhealthy" in status:
            add("Containere", "fail", f"{svc} kjører, men er ikke frisk", f"Se «docker logs {name}».")
        elif "Restarting" in status or "starting" in status:
            add("Containere", "warn", f"{svc} starter fortsatt ({status})", "Vent et minutt og kjør sjekken igjen.")
    bad = [r for r in ROWS if r[0] == "Containere"]
    if not bad:
        add("Containere", "ok", f"alle {len(expected)} tjenester kjører")


# =========================================================================== VPN og torrent
def public_ip():
    r = http("GET", "https://ipinfo.io/ip", timeout=15)
    return r.text.strip() if r.ok else ""


def check_vpn():
    if "gluetun" not in A.QBIT:
        return add("VPN", "warn", "ingen VPN – torrent-trafikken går rett ut", "Kjør veiviseren og velg en VPN-leverandør.")
    home = public_ip()
    code, out = docker_exec("gluetun", ["wget", "-qO-", "-T", "15", "https://ipinfo.io/ip"], timeout=40)
    vpn = (out or "").strip()
    if code != 0 or not re.fullmatch(r"[0-9a-fA-F.:]+", vpn):
        return add("VPN", "fail", "VPN-tunnelen kommer ikke ut på nettet",
                   "Se «docker logs gluetun». Som regel feil WireGuard-nøkkel i .env.")
    if home and vpn == home:
        return add("VPN", "fail", f"LEKKASJE: torrent-trafikken bruker din egen IP ({home})",
                   "Stopp qBittorrent («docker compose stop qbittorrent») og sjekk gluetun.")
    add("VPN", "ok", f"trafikken går gjennom VPN ({vpn}, hjemme: {home or 'ukjent'})")


def check_qbit():
    s = Session()
    h = {"Referer": A.QBIT, "Origin": A.QBIT}
    r = s.http("POST", f"{A.QBIT}/api/v2/auth/login", headers=h, form={"username": A.USER, "password": A.PASSWORD})
    if not (r.status in (200, 204) and "fail" not in r.text.lower()):
        return add("qBittorrent", "fail", f"kommer ikke inn ({r.status})", "Kjør «docker compose run --rm autoconfig».")
    info = s.http("GET", f"{A.QBIT}/api/v2/transfer/info", headers=h).json() or {}
    cats = s.http("GET", f"{A.QBIT}/api/v2/torrents/categories", headers=h).json() or {}
    missing = [c for c in ("tv", "movies") if c not in cats]
    st = info.get("connection_status", "ukjent")
    if st == "disconnected":
        add("qBittorrent", "fail", "innlogging virker, men qBittorrent har ingen nettforbindelse", "Sjekk VPN-en over.")
    elif missing:
        add("qBittorrent", "warn", f"mangler kategoriene {', '.join(missing)}", "Kjør autoconfig igjen.")
    else:
        add("qBittorrent", "ok", "innlogging, kategorier og nettforbindelse" +
            (" (bak brannmur – greit, men port forwarding gir raskere deling)" if st == "firewalled" else ""))


def check_nzbget():
    if not A.USENET:
        return
    import base64
    auth = base64.b64encode(f"{A.USER}:{A.PASSWORD}".encode()).decode()

    def rpc(method, params=()):
        r = http("POST", "http://nzbget:6789/jsonrpc", headers={"Authorization": f"Basic {auth}"},
                 json_body={"method": method, "params": list(params), "id": 1}, timeout=60)
        return r.json() if r.ok else None
    if not rpc("version"):
        return add("NZBGet", "fail", "kommer ikke inn", "Kjør autoconfig igjen.")
    host = env("USENET_HOST")
    if not host:
        return add("NZBGet", "warn", "ingen Usenet-server lagt inn", "Kjør veiviseren og svar ja på Usenet.")
    port = int(env("USENET_PORT", "563"))
    res = rpc("testserver", [host, port, env("USENET_USER"), env("USENET_PASSWORD"), port in (563, 443), "", 30])
    msg = (res or {}).get("result")
    err = (res or {}).get("error")
    if err:
        add("NZBGet", "warn", f"kunne ikke teste serveren ({err.get('message', err) if isinstance(err, dict) else err})")
    elif msg:
        hint = ("Senk USENET_CONNECTIONS i .env og kjør autoconfig." if "connection" in str(msg).lower()
                else "Sjekk brukernavn og passord hos Usenet-leverandøren.")
        add("NZBGet", "fail", f"Usenet-serveren svarer: {str(msg)[:120]}", hint)
    else:
        add("NZBGet", "ok", f"innlogging hos {host} virker")


# =========================================================================== indeksere og *arr
def check_prowlarr():
    p = arr("prowlarr", 9696, "v1")
    if not p:
        return add("Prowlarr", "fail", "fant ikke API-nøkkel")
    apps = {a.get("implementation") for a in p.get("applications")}
    ix = p.get("indexer")
    if not {"Sonarr", "Radarr"} <= apps:
        add("Prowlarr", "fail", f"ikke koblet til {', '.join({'Sonarr', 'Radarr'} - apps)}", "Kjør autoconfig igjen.")
    if not ix:
        return add("Prowlarr", "warn", "ingen indeksere ennå – ingenting kan søkes opp",
                   "Prowlarr → Indexers → Add Indexer. De synkes til Sonarr og Radarr av seg selv.")
    try:
        res = p.post("indexer/testall", {}) or []
        bad = [next((i["name"] for i in ix if i["id"] == t.get("id")), str(t.get("id")))
               for t in res if not t.get("isValid", True)]
    except RuntimeError:
        bad = []
    if bad:
        add("Prowlarr", "warn", f"{len(ix) - len(bad)} av {len(ix)} indeksere svarer. Feiler: {', '.join(bad)}",
            "Åpne indekseren i Prowlarr og trykk Test. Cloudflare-sider kan være blokkert.")
    else:
        add("Prowlarr", "ok", f"{len(ix)} indeksere svarer, koblet til Sonarr og Radarr")


def check_arr(app, port, kind):
    a = arr(app, port)
    if not a:
        return add(app.title(), "fail", "fant ikke API-nøkkel")
    roots = a.get("rootfolder")
    path = f"/data/{A.MOVIES if kind == 'movie' else A.TV}"
    root = next((r for r in roots if r["path"].rstrip("/") == path), None)
    if not root:
        add(app.title(), "fail", f"rotmappa {path} mangler", "Kjør autoconfig igjen.")
    elif root.get("accessible") is False:
        add(app.title(), "fail", f"får ikke tak i {path}", "Sjekk mediedisken/delingen og rettighetene (PUID/PGID).")
    try:
        tests = a.post("downloadclient/testall", {}) or []
    except RuntimeError:
        tests = []
    clients = {c["id"]: c["name"] for c in a.get("downloadclient")}
    bad = [clients.get(t.get("id"), "?") for t in tests if not t.get("isValid", True)]
    if not clients:
        add(app.title(), "fail", "ingen nedlastingsklienter", "Kjør autoconfig igjen når VPN-en er oppe.")
    elif bad:
        add(app.title(), "fail", f"nedlaster svarer ikke: {', '.join(bad)}", "Sjekk VPN/qBittorrent/NZBGet over, og kjør autoconfig.")
    server = "MediaBrowser" if A.MEDIA == "jellyfin" else "PlexServer"
    if not any(n.get("implementation") == server for n in a.get("notification")):
        add(app.title(), "warn", f"varsler ikke {A.MEDIA.title()} om nye filer", "Kjør autoconfig igjen.")
    health = [h for h in a.get("health") if h.get("type") in ("error", "warning")]
    for h in health[:4]:
        add(app.title(), "fail" if h.get("type") == "error" else "warn", f"{app.title()} melder: {h.get('message', '')[:140]}",
            h.get("wikiUrl", "") or "")
    if root and root.get("accessible") is not False and clients and not bad and not health:
        add(app.title(), "ok", f"rotmappe {path}, {len(clients)} nedlaster(e), ingen helsevarsler")


def check_hardlinks():
    """Prøver en ekte hardlink fra nedlastingsmappa til filmmappa, som PUID:PGID."""
    src = os.path.join(DATA, "downloads", ".sjekk-hardlink")
    dst = os.path.join(DATA, A.MOVIES, ".sjekk-hardlink")
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:
        msg = "ok"
        try:
            os.close(rfd)
            os.setgid(A.PGID)
            os.setuid(A.PUID)
            with open(src, "w") as f:
                f.write("test")
            os.link(src, dst)
            if os.stat(src).st_ino != os.stat(dst).st_ino:
                msg = "kopi i stedet for hardlink"
        except OSError as e:
            msg = f"{e.strerror or e} ({e.filename})"
        finally:
            for p in (dst, src):
                try:
                    os.remove(p)
                except OSError:
                    pass
            os.write(wfd, msg.encode())
            os._exit(0)
    os.close(wfd)
    os.waitpid(pid, 0)
    msg = os.read(rfd, 1000).decode()
    if msg == "ok":
        add("Disk", "ok", "skriving og hardlinks virker (nedlastinger flyttes uten å kopieres)")
    elif "cross-device" in msg.lower() or "Invalid cross" in msg:
        add("Disk", "warn", "nedlastinger og filmer ligger på ulike disker – filer kopieres i stedet",
            "Legg downloads/ på samme disk som filmene.")
    else:
        add("Disk", "fail", f"bruker {A.PUID} kan ikke skrive: {msg}",
            "Sjekk eier av mediemappa («ls -ln»), og at PUID/PGID i .env stemmer.")
    du = shutil.disk_usage(DATA)
    free = du.free / 1e9
    add("Disk", "ok" if free > FREE_WARN_GB else "warn", f"{free:.0f} GB ledig av {du.total / 1e9:.0f} GB",
        f"Mindre enn {FREE_WARN_GB:.0f} GB ledig – rydd eller bytt til større disk.")


# =========================================================================== mediaserver
def check_jellyfin():
    info = A.jf("GET", "/System/Info/Public").json() or {}
    if not info:
        return add("Jellyfin", "fail", "svarer ikke", "Se «docker logs jellyfin».")
    if not info.get("StartupWizardCompleted"):
        return add("Jellyfin", "fail", "oppstartsveiviseren er ikke fullført", "Kjør autoconfig igjen.")
    r = A.jf("POST", "/Users/AuthenticateByName", body={"Username": A.USER, "Pw": A.PASSWORD})
    token = (r.json() or {}).get("AccessToken") if r.ok else None
    if not token:
        return add("Jellyfin", "fail", f"kommer ikke inn som {A.USER}", "Passordet i Jellyfin og .env må være likt.")
    folders = A.jf("GET", "/Library/VirtualFolders", token).json() or []
    paths = {loc.rstrip("/") for f in folders for loc in f.get("Locations") or []}
    want = {f"/data/{A.MOVIES}", f"/data/{A.TV}"}
    counts = A.jf("GET", "/Items/Counts", token).json() or {}
    if not want <= paths:
        add("Jellyfin", "fail", f"mangler bibliotek for {', '.join(want - paths)}", "Kjør autoconfig igjen.")
    else:
        m, s = counts.get("MovieCount", 0), counts.get("SeriesCount", 0)
        add("Jellyfin", "ok" if m or s else "warn", f"bibliotekene finnes: {m} filmer, {s} serier",
            "Tomt? Vent til skanningen er ferdig, eller sjekk at mappene har innhold.")
    if env("HWACCEL") == "1":
        code, _ = docker_exec("jellyfin", ["test", "-e", "/dev/dri/renderD128"], timeout=20)
        enc = A.jf("GET", "/System/Configuration/encoding", token).json() or {}
        if code == 0 and enc.get("HardwareAccelerationType") not in (None, "", "none"):
            add("Jellyfin", "ok", f"maskinvare-transkoding på ({enc.get('HardwareAccelerationType')})")
        else:
            add("Jellyfin", "warn", "maskinvare-transkoding er ikke aktiv",
                "Sjekk at compose.dri.yaml står i COMPOSE_FILE, og kjør autoconfig.")


def check_plex():
    r = http("GET", f"{A.PLEX_URL}/identity", headers={"Accept": "application/json"})
    mc = (r.json() or {}).get("MediaContainer", {}) if r.ok else {}
    if not mc:
        return add("Plex", "fail", "svarer ikke", "Se «docker logs plex».")
    if str(mc.get("claimed")) not in ("1", "true", "True"):
        return add("Plex", "fail", "ikke knyttet til Plex-kontoen din", "Kjør veiviseren, logg inn i Plex, og kjør autoconfig.")
    tok = A.plex_pref("PlexOnlineToken")
    r = http("GET", f"{A.PLEX_URL}/library/sections", headers=A.plex_headers(tok))
    secs = ((r.json() or {}).get("MediaContainer", {}) or {}).get("Directory", []) if r.ok else []
    add("Plex", "ok" if len(secs) >= 2 else "warn", f"knyttet til kontoen, {len(secs)} bibliotek",
        "Kjør autoconfig igjen for å lage Filmer og Serier.")


def check_seerr():
    base = "http://seerr:5055/api/v1"
    pub = http("GET", f"{base}/settings/public").json() or {}
    if not pub:
        return add("Seerr", "fail", "svarer ikke", "Se «docker logs seerr».")
    if not pub.get("initialized"):
        return add("Seerr", "fail", "ikke satt opp", "Kjør autoconfig igjen.")
    key = (json.loads(read(f"{CFG}/seerr/settings.json") or "{}").get("main") or {}).get("apiKey", "")
    h = {"X-Api-Key": key}
    rad = http("GET", f"{base}/settings/radarr", headers=h).json()
    son = http("GET", f"{base}/settings/sonarr", headers=h).json()
    libs = http("GET", f"{base}/settings/{A.MEDIA}/library", headers=h).json()
    on = [l for l in libs if isinstance(l, dict) and l.get("enabled")] if isinstance(libs, list) else []
    if not (rad and son):
        add("Seerr", "fail", "ikke koblet til Radarr og Sonarr", "Kjør autoconfig igjen.")
    elif not on:
        add("Seerr", "warn", "ingen bibliotek slått på", f"Seerr → Settings → {A.MEDIA.title()} → Sync Libraries.")
    else:
        add("Seerr", "ok", f"koblet til Radarr, Sonarr og {len(on)} bibliotek")


def check_bazarr():
    m = re.search(r"^auth:\s*\n(?:[ \t]+.*\n)*?[ \t]+apikey:\s*['\"]?([A-Za-z0-9]+)",
                  read(f"{CFG}/bazarr/config/config.yaml"), re.M)
    if not m:
        return add("Bazarr", "warn", "fant ikke API-nøkkel")
    st = http("GET", "http://bazarr:6767/api/system/settings", headers={"X-API-KEY": m.group(1)}).json() or {}
    prov = (st.get("general") or {}).get("enabled_providers") or []
    if prov:
        add("Bazarr", "ok", f"{len(prov)} undertekstkilder: {', '.join(prov[:4])}")
    else:
        add("Bazarr", "warn", "ingen undertekstkilder valgt", "Bazarr → Settings → Providers.")


def check_whisper():
    st = http("GET", f"{A.WHISPER_URL}/docs", timeout=15)
    if not st.ok:
        hint = ("Start Whisper på den andre maskinen og sjekk at port 9000 er åpen." if A.WHISPER == "ekstern"
                else "Se «docker logs whisper».")
        why = f" (svarte {st.status})" if st.status else ""
        return add("Whisper", "fail", f"svarer ikke på {A.WHISPER_URL}{why}", hint)
    add("Whisper", "ok", f"svarer på {A.WHISPER_URL}")
    m = re.search(r"^auth:\s*\n(?:[ \t]+.*\n)*?[ \t]+apikey:\s*['\"]?([A-Za-z0-9]+)",
                  read(f"{CFG}/bazarr/config/config.yaml"), re.M)
    cfg = http("GET", "http://bazarr:6767/api/system/settings",
               headers={"X-API-KEY": m.group(1)}).json() or {} if m else {}
    if "whisperai" not in ((cfg.get("general") or {}).get("enabled_providers") or []):
        add("Whisper", "fail", "er ikke slått på i Bazarr", "Kjør autoconfig igjen: docker compose up autoconfig")
    elif ((cfg.get("whisperai") or {}).get("endpoint") or "").rstrip("/") != A.WHISPER_URL:
        add("Whisper", "warn", "Bazarr peker til en annen adresse",
            f"Bazarr → Settings → Providers → Whisper: sett endpoint til {A.WHISPER_URL}.")
    else:
        add("Whisper", "ok", "lagt inn i Bazarr" + (", lager automatisk" if A.WHISPER_AUTO else ", brukes fra manuelt søk"))


def check_extras():
    st = http("GET", "http://throttle:3010/api/status").json() or {}
    if st:
        add("Annet", "ok", f"kjøling: {st.get('status')} (temp {st.get('temp')} °C, prosessor {st.get('cpu')} %)")
    else:
        add("Annet", "warn", "kjøling svarer ikke", "Se «docker logs throttle».")
    if os.path.isfile(f"{CFG}/homepage/services.yaml"):
        add("Annet", "ok", f"dashboardet er laget (http://{A.DASH}:3000)")


# =========================================================================== hovedløp
def plan():
    """Alle sjekkene i rekkefølge. Brukes av «docker compose run --rm sjekk» og av kontrollpanelet."""
    steps = [("Containere", check_containers), ("VPN", check_vpn), ("qBittorrent", check_qbit)]
    if A.USENET:
        steps.append(("NZBGet", check_nzbget))
    steps += [("Prowlarr", check_prowlarr),
              ("Sonarr", lambda: check_arr("sonarr", 8989, "tv")),
              ("Radarr", lambda: check_arr("radarr", 7878, "movie")),
              ("Disk", check_hardlinks),
              (A.MEDIA.title(), check_jellyfin if A.MEDIA == "jellyfin" else check_plex),
              ("Seerr", check_seerr), ("Bazarr", check_bazarr)]
    if A.WHISPER != "av":
        steps.append(("Whisper", check_whisper))
    steps.append(("Annet", check_extras))
    return steps


def main():
    log("Sjekker installasjonen. Tar et halvt til et par minutter.\n")
    for name, fn in plan():
        section(name, fn)

    fails = [r for r in ROWS if r[1] == "fail"]
    warns = [r for r in ROWS if r[1] == "warn"]
    lines = ["", "═" * 66, "  RESULTAT", "═" * 66]
    if not fails and not warns:
        lines.append("  ✔ Alt virker.")
    else:
        lines.append(f"  {len(ROWS) - len(fails) - len(warns)} i orden, {len(warns)} å se på, {len(fails)} feil.")
        for g, st, text, fix in fails + warns:
            lines.append(f"  {SYMBOL[st]} {g}: {text}")
            if fix:
                lines.append(f"      → {fix}")
    lines.append(f"\n  Lagret i config/SJEKK.txt ({time.strftime('%Y-%m-%d %H:%M')}).")
    text = "\n".join(lines)
    log(text)
    write(f"{CFG}/SJEKK.txt", "\n".join(f"{SYMBOL[s]} {g}: {t}" + (f"  → {f}" if f else "") for g, s, t, f in ROWS)
          + "\n" + text + "\n", owner=(A.PUID, A.PGID))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
