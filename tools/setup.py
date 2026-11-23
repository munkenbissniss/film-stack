"""Veiviser: stiller noen få spørsmål og lager .env.
Kjøres med:  docker compose run --rm setup
Kan kjøres på nytt når som helst – svarene du ga sist blir standardvalg."""
import os
import secrets
import socket
import string
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from common import demux, docker, docker_info, http, log, ok, warn  # noqa: E402
import json  # noqa: E402

ENV_PATH = "/stack/.env"

PROVIDERS = [
    ("protonvpn", "ProtonVPN (WireGuard, støtter port forwarding)"),
    ("mullvad", "Mullvad (WireGuard)"),
    ("nordvpn", "NordVPN (WireGuard)"),
    ("surfshark", "Surfshark (WireGuard)"),
    ("airvpn", "AirVPN (WireGuard, støtter port forwarding)"),
    ("private internet access", "Private Internet Access (OpenVPN)"),
    ("custom", "Annen – egen WireGuard-konfig"),
    ("", "Ingen VPN (ikke anbefalt)"),
]


# --------------------------------------------------------------------------- hjelpere
def load_env():
    vals = {}
    try:
        for line in open(ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            vals[k.strip()] = v
    except OSError:
        pass
    return vals


def ask(prompt, default="", secret_hint=False, allow_empty=True):
    shown = default
    if secret_hint and default:
        shown = default[:4] + "…"
    while True:
        try:
            ans = input(f"  {prompt}" + (f" [{shown}]" if shown else "") + ": ").strip()
        except EOFError:
            ans = ""
        if not ans:
            ans = default
        if "'" in ans:
            warn("Bruk ikke apostrof (') i svaret.")
            continue
        if ans or allow_empty:
            return ans
        warn("Dette feltet må fylles ut.")


def yes(prompt, default=True):
    d = "J/n" if default else "j/N"
    try:
        a = input(f"  {prompt} [{d}]: ").strip().lower()
    except EOFError:
        a = ""
    return default if not a else a.startswith(("j", "y"))


def header(t):
    log(f"\n─── {t} " + "─" * max(0, 60 - len(t)))


def guess_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


def gen_password(n=16):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def plex_login(client_id):
    """Plex sin offisielle PIN-innlogging: du åpner en lenke og logger inn."""
    h = {"X-Plex-Product": "Plex Stack", "X-Plex-Client-Identifier": client_id,
         "Accept": "application/json"}
    r = http("POST", "https://plex.tv/api/v2/pins?strong=true", headers=h)
    pin = r.json() if r.ok else None
    if not pin:
        warn(f"Fikk ikke kontakt med plex.tv ({r.status}).")
        return ""
    url = ("https://app.plex.tv/auth#?clientID=" + client_id + "&code=" + pin["code"]
           + "&context%5Bdevice%5D%5Bproduct%5D=Plex%20Stack")
    log("\n  Åpne denne lenken i en nettleser og logg inn med Plex-kontoen din:\n")
    log("  " + url + "\n")
    log("  Venter på innlogging (inntil 5 minutter) …")
    end = time.time() + 300
    while time.time() < end:
        time.sleep(3)
        r = http("GET", f"https://plex.tv/api/v2/pins/{pin['id']}", headers=h)
        tok = (r.json() or {}).get("authToken") if r.ok else None
        if tok:
            ok("Innlogget i Plex.")
            return tok
    warn("Tidsavbrudd – du kan kjøre veiviseren på nytt senere.")
    return ""


def host_has_dri():
    """Sjekker /dev/dri på verten med en kortvarig container (bare på Linux)."""
    body = {"Image": "python:3.13-alpine", "Cmd": ["test", "-d", "/hostdev/dri"],
            "HostConfig": {"Binds": ["/dev:/hostdev:ro"]}}
    s, b = docker("POST", "/containers/create", body)
    if s != 201:
        return False
    cid = json.loads(b)["Id"]
    try:
        docker("POST", f"/containers/{cid}/start")
        s, b = docker("POST", f"/containers/{cid}/wait", timeout=60)
        return s == 200 and json.loads(b).get("StatusCode") == 0
    finally:
        docker("DELETE", f"/containers/{cid}?force=1")


VOLUME = "film-stack_media"


def test_share(typ, opts, device):
    """Kobler til delingen i en kortvarig container og viser innholdet."""
    tmp = "film-stack_sharetest"
    docker("DELETE", f"/volumes/{tmp}?force=1")
    s, b = docker("POST", "/volumes/create", {"Name": tmp, "Driver": "local",
                                              "DriverOpts": {"type": typ, "o": opts, "device": device}})
    if s != 201:
        return False, b.decode(errors="replace")
    s, b = docker("POST", "/containers/create", {
        "Image": "python:3.13-alpine", "Cmd": ["sh", "-c", "ls -1 /data | head -20 && touch /data/.filmstack-test && rm /data/.filmstack-test && echo SKRIV_OK"],
        "User": f"{PUID_GUESS[0]}:{PUID_GUESS[1]}", "HostConfig": {"Binds": [f"{tmp}:/data"]}})
    if s != 201:
        docker("DELETE", f"/volumes/{tmp}?force=1")
        return False, b.decode(errors="replace")
    cid = json.loads(b)["Id"]
    try:
        s, b = docker("POST", f"/containers/{cid}/start")
        if s not in (204, 304):
            return False, b.decode(errors="replace")
        docker("POST", f"/containers/{cid}/wait", timeout=60)
        s, b = docker("GET", f"/containers/{cid}/logs?stdout=1&stderr=1")
        return True, demux(b).decode(errors="replace")
    finally:
        docker("DELETE", f"/containers/{cid}?force=1")
        docker("DELETE", f"/volumes/{tmp}?force=1")


PUID_GUESS = [1000, 1000]


def choose_storage(old, puid, pgid):
    """Spør om mediemappa ligger lokalt eller på en nettverksdeling. Returnerer {} for lokal."""
    PUID_GUESS[:] = [puid, pgid]
    cur = {"nfs": "2", "cifs": "3"}.get(old.get("SHARE_TYPE", ""), "1")
    log("\n  Hvor ligger filmene og seriene?")
    log("    1. På denne maskinen (intern eller USB-disk, eller en mappe som allerede er montert)")
    log("    2. NFS-deling på en annen maskin (Linux, NAS)")
    log("    3. SMB-deling (Windows-deling, NAS, Mac-deling)")
    sel = ask("Velg", cur)
    if sel not in ("2", "3"):
        return {}
    while True:
        server = ask("Serverens IP-adresse", old.get("SHARE_SERVER", ""), allow_empty=False)
        if sel == "2":
            log("  Stien som er delt (står i /etc/exports på serveren, f.eks. /srv/media)")
            path = ask("Delt sti", old.get("SHARE_PATH", ""), allow_empty=False)
            typ, device = "nfs", ":" + path
            opts = f"addr={server},rw,nfsvers=4,soft,timeo=150,retrans=3"
            extra = {}
        else:
            log("  Navnet på delingen, f.eks. Media (som i \\\\server\\Media)")
            path = ask("Delingsnavn", old.get("SHARE_PATH", ""), allow_empty=False).strip("/\\")
            user = ask("Brukernavn på serveren", old.get("SHARE_USER", ""), allow_empty=False)
            pw = ask("Passord", old.get("SHARE_PASSWORD", ""), True, False)
            if "," in pw or "," in user:
                warn("Brukernavn og passord kan ikke inneholde komma (,) for SMB-deling.")
                continue
            typ, device = "cifs", f"//{server}/{path}"
            opts = (f"addr={server},username={user},password={pw},uid={puid},gid={pgid},"
                    "file_mode=0664,dir_mode=0775,vers=3.0")
            extra = {"SHARE_USER": user, "SHARE_PASSWORD": pw}
        log("  Tester tilkoblingen …")
        good, out = test_share(typ, opts, device)
        if good and out.strip():
            log("  Innhold:")
            for line in out.strip().splitlines():
                if line != "SKRIV_OK":
                    log("    " + line)
            if "SKRIV_OK" in out:
                ok("Tilkoblet, og skriving fungerer.")
            else:
                warn(f"Tilkoblet, men bruker {puid} kan ikke skrive. Sjekk eier/rettigheter på serveren "
                     "(samme bruker-ID på begge maskiner, «id -u»).")
        else:
            warn("Fikk ikke koblet til: " + (out.strip()[-300:] or "ukjent feil"))
            log("  Sjekk IP, sti/navn, at delingen tillater denne maskinens IP, og brannmuren.")
        if good and yes("Bruke denne delingen?", True):
            break
        if not yes("Prøve på nytt?", True):
            return {}
    remove_old_volume(typ, opts, device)
    return {"SHARE_TYPE": typ, "SHARE_SERVER": server, "SHARE_PATH": path,
            "SHARE_OPTS": opts, "SHARE_DEVICE": device, **extra}


def remove_old_volume(typ, opts, device):
    """Docker husker en deling til volumet slettes. Fjern det gamle hvis noe er endret."""
    s, b = docker("GET", f"/volumes/{VOLUME}")
    if s != 200:
        return
    o = json.loads(b).get("Options") or {}
    if o.get("type") == typ and o.get("o") == opts and o.get("device") == device:
        return
    s, _ = docker("DELETE", f"/volumes/{VOLUME}")
    if s == 409:
        warn("Den gamle delingen er i bruk. Kjør «docker compose down» og så veiviseren igjen.")


def as_path(p):
    """Relative stier må starte med ./ – ellers tolker Docker dem som et navngitt volum."""
    p = p.strip().replace("\\", "/")
    if p and not (p.startswith(("/", "./", "../", "~")) or (len(p) > 1 and p[1] == ":")):
        p = "./" + p
    return p.rstrip("/") if len(p) > 1 else p


def q(v):
    return f"'{v}'"


# --------------------------------------------------------------------------- veiviser
def main():
    old = load_env()
    info = docker_info()
    os_name = info.get("OperatingSystem", "")
    kernel = info.get("KernelVersion", "").lower()
    desktop = ("Docker Desktop" in os_name or "orbstack" in kernel or "linuxkit" in kernel
               or "OrbStack" in os_name)
    native_linux = bool(info) and not desktop
    has_dri = native_linux and host_has_dri()

    log("\n╔══════════════════════════════════════════════════════════════╗")
    log("║               Film-stack – oppsettsveiviser                  ║")
    log("╚══════════════════════════════════════════════════════════════╝")
    log(f"  Docker: {os_name or 'ukjent'}")
    if native_linux:
        log("  Linux oppdaget" + (": maskinvare-transkoding (/dev/dri) er tilgjengelig." if has_dri else "."))
    else:
        log("  Docker Desktop/OrbStack oppdaget (Mac/Windows): transkoding skjer med prosessoren.")
    log("  Trykk Enter for å godta forslaget i [klammer].")

    # PUID/PGID: eieren av prosjektmappa (Linux). På Docker Desktop er 1000 riktig.
    st = os.stat("/stack")
    puid, pgid = (st.st_uid, st.st_gid) if native_linux and st.st_uid != 0 else (1000, 1000)

    header("Mediaserver")
    log("    1. Jellyfin – gratis og åpen kildekode, alt inkludert (anbefalt)")
    log("    2. Plex – trenger Plex-konto, noe krever Plex Pass (f.eks. maskinvare-transkoding)")
    media = "plex" if ask("Velg", "2" if old.get("MEDIA_SERVER") == "plex" else "1") == "2" else "jellyfin"

    header("Grunnleggende")
    tz = ask("Tidssone", old.get("TZ", "Europe/Oslo"))
    share = choose_storage(old, puid, pgid)
    if share:
        data_root = old.get("DATA_ROOT", "./media")
    else:
        log("\n  Oppgi stien til mediemappa på DENNE maskinen, f.eks.:")
        log("    Linux: /mnt/media     Mac: /Volumes/Media     Windows: D:/Media")
        data_root = as_path(ask("Mediemappe", old.get("DATA_ROOT", "/mnt/media" if native_linux else ""),
                                allow_empty=False))
    log("\n  Mappene for filmer og serier, relativt til mediemappa (f.eks. movies eller Media/movies).")
    log("  Tomt = finn automatisk: Filmer/Movies og Serier/TV, øverst eller én mappe ned.")
    movies_dir = ask("Filmmappe", old.get("MOVIES_DIR", ""))
    tv_dir = ask("Seriemappe", old.get("TV_DIR", ""))
    config_root = as_path(ask("Mappe for innstillinger", old.get("CONFIG_ROOT", "./config")))

    header("Felles innlogging")
    log("  Samme brukernavn og passord settes på " + ("Jellyfin, Seerr, " if media == "jellyfin" else "")
        + "Sonarr, Radarr, Prowlarr, Bazarr,")
    log("  qBittorrent og NZBGet.")
    admin_user = ask("Brukernavn", old.get("ADMIN_USER", "admin"))
    default_pw = old.get("ADMIN_PASSWORD") or gen_password()
    admin_pw = ask("Passord (Enter = bruk forslaget)", default_pw)

    header("Nettverk")
    ip = old.get("LAN_IP") or guess_ip()
    if not native_linux:
        log("  Finn Macens/PC-ens IP på hjemmenettet:")
        log("    Mac: ipconfig getifaddr en0        Windows: ipconfig (IPv4-adresse)")
        if ip.startswith(("192.168.65.", "172.", "10.0.2.")):
            ip = old.get("LAN_IP", "")
    lan_ip = ask("Maskinens IP-adresse på hjemmenettet", ip, allow_empty=False)
    hostname = socket.gethostname() if native_linux else ""
    dash_host = ask("Navn/IP du bruker i nettleseren (eller Tailscale-navn)",
                    old.get("DASHBOARD_HOST", lan_ip))

    header("VPN for nedlasting")
    for i, (_, label) in enumerate(PROVIDERS, 1):
        log(f"    {i}. {label}")
    cur = old.get("VPN_SERVICE_PROVIDER", "protonvpn") if old.get("NO_VPN") != "1" else ""
    cur_idx = next((i for i, (p, _) in enumerate(PROVIDERS, 1) if p == cur), 1)
    sel = ask("Velg", str(cur_idx))
    try:
        provider = PROVIDERS[int(sel) - 1][0]
    except (ValueError, IndexError):
        provider = "protonvpn"
    vpn = {}
    if provider:
        vpn["VPN_SERVICE_PROVIDER"] = provider
        if provider == "private internet access":
            vpn["VPN_TYPE"] = "openvpn"
            vpn["OPENVPN_USER"] = ask("PIA-brukernavn", old.get("OPENVPN_USER", ""), allow_empty=False)
            vpn["OPENVPN_PASSWORD"] = ask("PIA-passord", old.get("OPENVPN_PASSWORD", ""), True, False)
        else:
            vpn["VPN_TYPE"] = "wireguard"
            log("  Last ned en WireGuard-konfig fra leverandøren. Der står «PrivateKey = …» og «Address = …».")
            vpn["WIREGUARD_PRIVATE_KEY"] = ask("PrivateKey", old.get("WIREGUARD_PRIVATE_KEY", ""), True, False)
            vpn["WIREGUARD_ADDRESSES"] = ask("Address (Enter = tom for ProtonVPN)", old.get("WIREGUARD_ADDRESSES", ""))
        if provider != "custom":
            vpn["VPN_SERVER_COUNTRIES"] = ask("Land", old.get("VPN_SERVER_COUNTRIES", "Norway"))
        if provider in ("protonvpn", "airvpn", "private internet access"):
            vpn["VPN_PORT_FORWARDING"] = "on" if yes("Slå på port forwarding (raskere deling)?", True) else "off"

    profiles = [p for p in old.get("COMPOSE_PROFILES", "").split(",") if p]
    opt = []

    header("Usenet (nyhetsgrupper) med NZBGet")
    log("  Trenger et abonnement hos en Usenet-leverandør (f.eks. Newshosting, Eweka,")
    log("  Frugal) og minst én NZB-indekser (legges inn i Prowlarr etterpå).")
    usenet = {}
    if yes("Bruke Usenet?", "usenet" in profiles):
        opt.append("usenet")
        usenet["USENET_HOST"] = ask("Server", old.get("USENET_HOST", "news.newshosting.com"))
        usenet["USENET_PORT"] = ask("Port (563 = SSL)", old.get("USENET_PORT", "563"))
        usenet["USENET_USER"] = ask("Brukernavn hos leverandøren", old.get("USENET_USER", ""), allow_empty=False)
        usenet["USENET_PASSWORD"] = ask("Passord hos leverandøren", old.get("USENET_PASSWORD", ""), True, False)
        usenet["USENET_CONNECTIONS"] = ask("Antall tilkoblinger (sjekk abonnementet ditt)",
                                           old.get("USENET_CONNECTIONS", "30"))

    header("Valgfrie apper")
    if yes(f"Wizarr – invitasjonslenker til {'Jellyfin' if media == 'jellyfin' else 'Plex'} for venner/familie?", "wizarr" in profiles):
        opt.append("wizarr")
    if yes("Autobrr – for private trackere (IRC-annonser)?", "autobrr" in profiles):
        opt.append("autobrr")

    header("AI-undertekster (Whisper)")
    log("  Whisper lytter på lydsporet og skriver undertekster når ingen finnes på nett.")
    log("  Den skriver på språket som snakkes, og kan i tillegg oversette til engelsk.")
    log("  Den kan ikke oversette til norsk: en engelsk film får engelske undertekster.")
    old_w = old.get("WHISPER", "av")
    whisper, whisper_url, whisper_auto = "av", "http://whisper:9000", "0"
    whisper_model = old.get("WHISPER_MODEL", "turbo")
    if yes("Bruke AI-undertekster?", old_w != "av"):
        log("\n  Hvor skal Whisper kjøre?")
        log("    1. På denne maskinen, med prosessoren (virker overalt, men tar tid:")
        log("       en film kan ta like lang tid som den varer, eller lenger på eldre maskiner)")
        log("    2. På denne maskinen, med NVIDIA-skjermkort (raskt, krever NVIDIA Container Toolkit)")
        log("    3. På en annen maskin på nettverket (f.eks. en PC med NVIDIA-skjermkort, se mappa whisper-ekstern)")
        default = {"gpu": "2", "ekstern": "3"}.get(old_w, "1")
        valg = ask("Velg", default)
        whisper = {"2": "gpu", "3": "ekstern"}.get(valg, "cpu")
        if whisper == "ekstern":
            whisper_url = ask("Adresse til Whisper på den andre maskinen (f.eks. http://192.168.1.50:9000)",
                              old.get("WHISPER_URL", "") if old_w == "ekstern" else "", allow_empty=False).rstrip("/")
            if not whisper_url.startswith("http"):
                whisper_url = "http://" + whisper_url
        else:
            log("\n  AI-modell (lastes ned første gang den brukes):")
            log("    1. small  – raskest, greit for engelsk, svakere på norsk (0,5 GB)")
            log("    2. turbo  – god på norsk og engelsk, rask for sin størrelse (1,6 GB, anbefalt)")
            log("    3. large-v3 – best, men tregest (3 GB, helst med skjermkort)")
            cur = {"small": "1", "large-v3": "3"}.get(whisper_model, "2")
            whisper_model = {"1": "small", "3": "large-v3"}.get(ask("Velg", cur), "turbo")
        log("\n  Whisper kan lage tekst automatisk når ingen andre kilder har en. Da senkes")
        log("  kravet til treffsikkerhet i Bazarr for alle kilder, og maskinen kan jobbe i dagevis")
        log("  med hele biblioteket. Ellers velger du Whisper selv, film for film, i Bazarr.")
        whisper_auto = "1" if yes("Lage AI-undertekster automatisk?", old.get("WHISPER_AUTO") == "1") else "0"
        if whisper in ("cpu", "gpu"):
            opt.append("whisper")

    header("Rolig modus")
    log("  Begrenser hvor mye prosessor bakgrunnsappene får bruke. Mediaserveren begrenses aldri,")
    log("  så avspillingen påvirkes ikke. Anbefalt for bærbare og eldre maskiner.")
    cores = os.cpu_count() or 4
    quiet = yes(f"Slå på rolig modus? (maskinen har {cores} tråder)",
                "compose.quiet.yaml" in old.get("COMPOSE_FILE", "") or (not old and cores <= 4))

    token = old.get("PLEX_ACCOUNT_TOKEN", "")
    client_id = old.get("PLEX_CLIENT_ID") or str(uuid.uuid4())
    if media == "plex":
        header("Plex-konto")
        log("  Med Plex-innlogging kan alt kobles automatisk: serveren knyttes til kontoen")
        log("  din, bibliotekene lages, og Seerr, Tautulli, Sonarr og Radarr kobles til Plex.")
        if token and not yes("Allerede innlogget i Plex. Logge inn på nytt?", False):
            pass
        elif yes("Logge inn i Plex nå?", True):
            token = plex_login(client_id) or token

    # ------------------------------------------------------------- skriv .env
    files = ["compose.yaml"]
    if native_linux and media == "plex":
        files.append("compose.plexhost.yaml")
    if has_dri:
        files.append("compose.dri.yaml")
    if share:
        files.append("compose.netshare.yaml")
    if not provider:
        files.append("compose.novpn.yaml")
    if quiet:
        files.append("compose.quiet.yaml")
    if whisper == "gpu":
        files.append("compose.whispergpu.yaml")
    allowed = sorted({f"{h}:3000" for h in ("localhost", "127.0.0.1", lan_ip, dash_host,
                                            f"{hostname}.local" if hostname else "") if h})

    lines = [
        "# Laget av veiviseren (docker compose run --rm setup). Kan redigeres for hånd.",
        "", "# --- Grunnleggende",
        f"TZ={q(tz)}", f"PUID={puid}", f"PGID={pgid}",
        f"DATA_ROOT={q(data_root)}", f"CONFIG_ROOT={q(config_root)}",
        f"MOVIES_DIR={q(movies_dir)}", f"TV_DIR={q(tv_dir)}",
        "", "# --- Felles innlogging",
        f"ADMIN_USER={q(admin_user)}", f"ADMIN_PASSWORD={q(admin_pw)}",
        "", "# --- Nettverk",
        f"LAN_IP={q(lan_ip)}", f"DASHBOARD_HOST={q(dash_host)}",
        f"HOMEPAGE_ALLOWED_HOSTS={q(','.join(allowed))}",
        f"MEDIA_SERVER={media}",
        f"PLEX_HOST_MODE={1 if native_linux and media == 'plex' else 0}",
        f"PLEX_INTERNAL_URL={q('http://host.docker.internal:32400' if native_linux and media == 'plex' else 'http://plex:32400')}",
        f"HWACCEL={1 if has_dri else 0}",
        "", "# --- VPN",
        f"NO_VPN={0 if provider else 1}",
        f"QBIT_HOST={'gluetun' if provider else 'qbittorrent'}",
    ]
    lines += [f"{k}={q(v)}" for k, v in vpn.items()]
    if share:
        lines += ["", "# --- Nettverksdeling for mediemappa"] + [f"{k}={q(v)}" for k, v in share.items()]
    if usenet:
        lines += ["", "# --- Usenet"] + [f"{k}={q(v)}" for k, v in usenet.items()]
    lines += [
        "", "# --- Plex (brukes bare når MEDIA_SERVER=plex)",
        f"PLEX_ACCOUNT_TOKEN={q(token)}", f"PLEX_CLIENT_ID={q(client_id)}",
        "LIBRARY_LANGUAGE='en-US'", "SUBTITLE_LANGS='no,en'",
        "", "# --- AI-undertekster (Whisper): av | cpu | gpu | ekstern",
        f"WHISPER={whisper}", f"WHISPER_URL={q(whisper_url)}",
        f"WHISPER_MODEL={q(whisper_model)}", f"WHISPER_AUTO={whisper_auto}",
        "", "# --- Recyclarr (TRaSH Guides-maler)",
        "RECYCLARR_SONARR_TEMPLATE='web-1080p'", "RECYCLARR_RADARR_TEMPLATE='hd-bluray-web'",
        "", "# --- Hvilke filer og valgfrie apper Docker Compose bruker",
        "COMPOSE_PATH_SEPARATOR=':'",
        f"COMPOSE_FILE={q(':'.join(files))}",
        f"COMPOSE_PROFILES={q(','.join([media] + opt))}",
        "",
    ]
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    try:
        os.chmod(ENV_PATH, 0o600)
        os.chown(ENV_PATH, st.st_uid, st.st_gid)
    except OSError:
        pass

    log("\n╔══════════════════════════════════════════════════════════════╗")
    log("║  Ferdig! .env er lagret.                                     ║")
    log("╚══════════════════════════════════════════════════════════════╝")
    log("  Start alt (første gang tar det noen minutter å laste ned):\n")
    log("      docker compose up -d")
    log("      docker compose logs -f autoconfig\n")
    log(f"  Når autoconfig sier «Ferdig», åpner du dashboardet:  http://{dash_host}:3000")
    log(f"  Kontrollpanel (status, innstillinger, logger):        http://{dash_host}:3010")
    log(f"  {'Jellyfin' if media == 'jellyfin' else 'Plex'}:  http://{dash_host}:" + ("8096" if media == "jellyfin" else "32400/web"))
    log(f"  Innlogging overalt: {admin_user} / {admin_pw}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\nAvbrutt – ingenting er lagret.")
        sys.exit(1)
