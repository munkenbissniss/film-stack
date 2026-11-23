"""Kvalitetsvakt: sørger for at Sonarr og Radarr bare henter MIN_RESOLUTION (standard 1080p) eller bedre.

• slår av kvaliteter under grensen (og BR-DISK/Raw-HD) i alle kvalitetsprofiler
• profiler som da blir tomme (Any, SD, HD-720p …) slettes – seriene/filmene som brukte dem
  flyttes til hovedprofilen (TRaSH-profilen fra Recyclarr)
Kjøres av autoconfig og deretter hvert 15. minutt av kjøletjenesten, fordi Recyclarr
skriver tilbake guidens kvaliteter hver natt.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import CFG, http, log, xml_apikey  # noqa: E402

APPS = {
    "sonarr": (8989, "series", "seriesIds", ["WEB-1080p", "WEB-2160p", "HD-1080p"]),
    "radarr": (7878, "movie", "movieIds", ["HD Bluray + WEB", "UHD Bluray + WEB", "HD-1080p"]),
}
BAD = {"BR-DISK", "Raw-HD"}


def _api(app, port, key, method, path, body=None):
    return http(method, f"http://{app}:{port}/api/v3/{path}", json_body=body, timeout=60,
                headers={"X-Api-Key": key, "Accept": "application/json"})


def _good(q, minres):
    return (q or {}).get("resolution", 0) >= minres and (q or {}).get("name") not in BAD


def filter_profile(p, minres):
    """Slår av for dårlige kvaliteter. Returnerer (endret, id-er som fortsatt er tillatt)."""
    changed, remaining = False, []
    for it in p.get("items", []):
        if it.get("quality"):
            ok_ = _good(it["quality"], minres)
            ident = it["quality"]["id"]
        else:                                   # kvalitetsgruppe, f.eks. «WEB 1080p»
            subs = it.get("items", [])
            ok_ = bool(subs) and all(_good(s.get("quality"), minres) for s in subs)
            ident = it.get("id")
        if it.get("allowed") and not ok_:
            it["allowed"] = False
            changed = True
        if it.get("allowed"):
            remaining.append(ident)
    if remaining and p.get("cutoff") not in remaining:
        p["cutoff"] = remaining[-1]             # lista går fra dårligst til best
        changed = True
    return changed, remaining


def enforce(minres=None, verbose=True):
    minres = int(minres or os.environ.get("MIN_RESOLUTION") or 1080)
    say = log if verbose else (lambda *_: None)
    summary = {}
    for app, (port, kind, idfield, preferred) in APPS.items():
        key = xml_apikey(f"{CFG}/{app}/config.xml")
        if not key:
            continue
        r = _api(app, port, key, "GET", "qualityprofile")
        if not r.ok:
            continue
        profiles = r.json() or []
        res = {p["id"]: filter_profile(p, minres) for p in profiles}
        keep = [p for p in profiles if res[p["id"]][1]]
        empty = [p for p in profiles if not res[p["id"]][1]]
        if not keep:
            say(f"  ⚠ {app}: ingen profil har {minres}p eller bedre – endrer ingenting")
            continue
        main = next((p for n in preferred for p in keep if p["name"].lower() == n.lower()), keep[0])
        fixed = 0
        for p in keep:
            if res[p["id"]][0] and _api(app, port, key, "PUT", f"qualityprofile/{p['id']}", p).ok:
                fixed += 1
        moved = removed = 0
        if empty:
            media = _api(app, port, key, "GET", kind).json() or []
            for p in empty:
                ids = [m["id"] for m in media if m.get("qualityProfileId") == p["id"]]
                if ids and _api(app, port, key, "PUT", f"{kind}/editor",
                                {idfield: ids, "qualityProfileId": main["id"]}).ok:
                    moved += len(ids)
                if _api(app, port, key, "DELETE", f"qualityprofile/{p['id']}").ok:
                    removed += 1
        summary[app] = (fixed, removed, moved, main["name"])
        if fixed or removed or moved:
            say(f"  ✔ {app}: {fixed} profil(er) strammet inn, {removed} fjernet, "
                f"{moved} {'serier' if app == 'sonarr' else 'filmer'} flyttet til «{main['name']}»")
        else:
            say(f"  ✔ {app}: alle profiler er {minres}p eller bedre")
    return summary
