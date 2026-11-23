# Endringer

## 3.2.0
- Kontrollpanel på :3010 (erstatter kjølesiden), med innlogging. Viser hele stacken som grønt, gult eller rødt
  med forklaringer og knapper, og sjekker hvert kvarter.
- Trygge feil rettes av seg selv: apper som har stoppet startes på nytt, VPN-en startes på nytt, og qBittorrent
  stoppes ved VPN-lekkasje. Høyst to forsøk per seks timer.
- Innstillinger for kvalitet, disk (varsel og automatisk pause før disken blir full), fartsgrenser med tidsplan,
  automatiske oppdateringer (venter hvis noen ser på noe) og indeksere (status, statistikk, test, av/på).
- Debug-logging med én knapp, slås av etter 24 timer. Loggpakke (zip) der passord, nøkler, token,
  e-postadresser og offentlige IP-er er sladdet.
- Dashboardkortet «Kjøling» heter nå «Kontrollpanel» og viser status.

## 3.1.0
- AI-undertekster med Whisper (valgfritt i veiviseren). Kjører på prosessoren, et NVIDIA-skjermkort
  eller en annen maskin på nettverket (mappa `whisper-ekstern`). Legges inn i Bazarr automatisk.
- Velg modell (small, turbo eller large-v3), og om undertekstene skal lages automatisk eller ved manuelt søk.
- Sjekken tester at Whisper svarer og er slått på i Bazarr.
- Rolig modus og kjøling begrenser Whisper som de andre bakgrunnsappene.

## 3.0.0
- Ny sjekk: «docker compose run --rm sjekk» tester VPN, nedlastere, indeksere, hardlinks og mediaserver, og kjøres av startskriptet til slutt.
- Nytt navn: Film-stack (tidligere Plex-stack).
- Jellyfin er standard mediaserver: gratis og åpen kildekode, med maskinvare-transkoding uten abonnement.
  Plex kan fortsatt velges i veiviseren.
- Autoconfig fullfører Jellyfins oppstartsveiviser, lager administrator, bibliotekene Filmer og Serier,
  API-nøkkel og maskinvare-transkoding (VAAPI) når /dev/dri finnes.
- Sonarr og Radarr ber Jellyfin oppdatere biblioteket ved nye filer. En gammel Plex-varsling fjernes.
- Seerr, Prefetcharr, Wizarr og dashboardet kobles til den serveren du velger.
- Tautulli er bare med når Plex brukes.
- Nettsider og README på norsk og engelsk.

## 2.0.0
- Veiviser (`docker compose run --rm setup`) for Linux, macOS og Windows.
- Autoconfig setter opp og kobler sammen alle appene via API-ene deres, og kan kjøres om igjen trygt.
- Mediemappe lokalt eller som NFS-/SMB-deling, med test av delingen i veiviseren.
- Usenet med NZBGet, i tillegg til qBittorrent bak VPN (gluetun).
- Kvalitetsvakt: bare 1080p eller bedre, i tillegg til TRaSH-profiler via Recyclarr.
- Kjøling: belastning på dashboardet og automatisk nedkjøling av bakgrunnsapper.
- Rolig modus for bærbare og eldre maskiner.
- Egne pakker og startskript for Linux, macOS og Windows.
