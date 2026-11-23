"""Kjører før alt annet: lager mapper med riktige eiere, så ingen container
starter mot feil rettigheter. Avslutter alltid med 0 (feil logges)."""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import CFG, DATA, env, log, media_dirs, ok, step, warn, write  # noqa: E402

PUID, PGID = int(env("PUID", "1000")), int(env("PGID", "1000"))
APPS = ["jellyfin", "plex", "plex-transcode", "gluetun", "qbittorrent", "prowlarr", "sonarr", "radarr",
        "bazarr", "tautulli", "homepage", "recyclarr", "recyclarr/configs", "prefetcharr",
        "prefetcharr/log", "wizarr", "autobrr", "nzbget", "throttle"]


def chown(path, uid=PUID, gid=PGID):
    try:
        os.chown(path, uid, gid)
    except OSError:
        pass  # Docker Desktop (Mac/Windows) håndterer rettigheter selv


def mkdir(path, uid=PUID, gid=PGID):
    new = not os.path.isdir(path)
    os.makedirs(path, exist_ok=True)
    if new or os.stat(path).st_uid == 0:
        chown(path, uid, gid)
    return new


def prepare_media():
    """Kjøres som PUID:PGID – da får mappene riktig eier, også på NFS (der root ikke får skrive)."""
    if not os.access(DATA, os.W_OK):
        warn(f"Kan ikke skrive til mediemappa som bruker {PUID}. Sjekk DATA_ROOT/delingen og rettighetene.")
        return
    movies, tv = media_dirs()
    for d in (movies, tv, "downloads", "downloads/incomplete", "downloads/movies", "downloads/tv",
              "downloads/usenet/intermediate", "downloads/usenet/completed",
              "downloads/usenet/completed/tv", "downloads/usenet/completed/movies"):
        p = os.path.join(DATA, d)
        if not os.path.isdir(p):
            try:
                os.makedirs(p, exist_ok=True)
                ok(f"laget {d}/")
            except OSError as e:
                warn(f"kunne ikke lage {d}/: {e}")
    ok(f"filmer: {movies}/   serier: {tv}/")
    try:
        if not os.listdir(os.path.join(DATA, movies)) and not os.listdir(os.path.join(DATA, tv)):
            warn("Film- og seriemappene er tomme. Peker mediemappa/delingen riktig?")
    except OSError:
        pass


def run_as_user(fn):
    """Kjører fn i en egen prosess som PUID:PGID."""
    pid = os.fork()
    if pid == 0:
        code = 0
        try:
            os.setgid(PGID)
            os.setuid(PUID)
            fn()
        except Exception as e:
            warn(f"mediemappe: {e}")
            code = 1
        finally:
            sys.stdout.flush()
            os._exit(code)
    os.waitpid(pid, 0)


def main():
    step("Konfigmapper")
    for app in APPS:
        mkdir(os.path.join(CFG, app))
    mkdir(os.path.join(CFG, "seerr"), 1000, 1000)  # Seerr kjører alltid som bruker 1000
    ok(f"{len(APPS) + 1} mapper klare")

    # Prefetcharr leser en fil. Finnes den ikke, lager Docker en mappe i stedet.
    pf = os.path.join(CFG, "prefetcharr", "config.toml")
    if os.path.isdir(pf):
        shutil.rmtree(pf, ignore_errors=True)
    if not os.path.isfile(pf):
        write(pf, "# Fylles ut av autoconfig\n", owner=(PUID, PGID))

    # Dashboard-maler (overskrives av autoconfig med nøkler og widgets)
    for f in os.listdir("/stack/homepage"):
        dst = os.path.join(CFG, "homepage", f)
        if not os.path.exists(dst):
            shutil.copy(os.path.join("/stack/homepage", f), dst)
            chown(dst)

    step("Mediedisk")
    run_as_user(prepare_media)
    log("\ninit ferdig.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # stopp aldri hele stacken på grunn av init
        warn(f"init: {e}")
    sys.exit(0)
