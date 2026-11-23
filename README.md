# Film-stack – automatisk mediaserver på Docker

**Norsk** · [English](README.en.md) · [Nettside](https://munkenbissniss.github.io/film-stack/) · [Alle prosjekter](https://munkenbissniss.github.io/)

**Én veiviser, én kommando, og alt er satt opp.** Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent bak VPN, NZBGet, Bazarr, Seerr og et dashboard kjører i Docker på **Linux, macOS og Windows**. Appene settes opp og kobles sammen automatisk via API-ene sine, så du slipper å klikke deg gjennom tjue innstillingssider.

**Jellyfin** er standard: gratis, åpen kildekode, og alt er med, også maskinvare-transkoding. Foretrekker du **Plex**, velger du det i veiviseren.

> Tidligere Plex-stack. Inspirert av [DonMcD/ultimate-plex-stack](https://github.com/DonMcD/ultimate-plex-stack).

## Last ned

| System | Pakke | Start med |
|---|---|---|
| 🐧 **Linux** (anbefalt) | [film-stack-linux.zip](../../releases/latest/download/film-stack-linux.zip) | `./start.sh` |
| 🍎 **macOS 14+** | [film-stack-macos.zip](../../releases/latest/download/film-stack-macos.zip) | dobbeltklikk `Start.command` |
| 🪟 **Windows 10/11** | [film-stack-windows.zip](../../releases/latest/download/film-stack-windows.zip) | dobbeltklikk `Start.bat` |

Alle versjoner ligger under [Releases](../../releases). Startskriptet kjører veiviseren første gang, starter alt og viser oppsettet mens det skjer. Du kan også gjøre det for hånd:

```
docker compose run --rm setup      # 1. svar på noen spørsmål (2 min)
docker compose up -d               # 2. start alt
docker compose logs -f autoconfig  # 3. se at alt settes opp av seg selv
```

## Hva du får

| App | Adresse | Gjør | Settes opp automatisk |
|---|---|---|---|
| **Homepage** | :3000 | Dashboard med alt | ✔ alle widgets og nøkler |
| **Jellyfin** | :8096 | Mediaserver (standard) | ✔ administrator, bibliotekene Filmer og Serier, norsk metadata, maskinvare-transkoding |
| Plex | :32400/web | Mediaserver (valgfri i stedet for Jellyfin) | ✔ knyttes til kontoen din, bibliotekene lages |
| Sonarr / Radarr | :8989 / :7878 | Serier / filmer | ✔ innlogging, rotmapper, nedlastere, oppdatering av mediaserveren, hardlinks |
| Prowlarr | :9696 | Indeksere | ✔ innlogging, FlareSolverr, kobles til Sonarr/Radarr |
| qBittorrent | :8080 | Torrent bak VPN | ✔ passord, mapper, kategorier |
| NZBGet | :6789 | Usenet (valgfri) | ✔ server, passord, mapper, kategorier |
| Bazarr | :6767 | Undertekster | ✔ Sonarr/Radarr, språkprofil (norsk + engelsk) |
| Whisper | :9000 | AI-undertekster når ingen finnes (valgfri) | ✔ lagt inn i Bazarr |
| Seerr | :5055 | Ønskeliste for familie/venner | ✔ innlogging med Jellyfin eller Plex, Sonarr/Radarr, bibliotek |
| Tautulli | :8181 | Hvem ser hva (bare med Plex) | ✔ Plex |
| Recyclarr | – | TRaSH Guides-kvalitetsprofiler | ✔ synker hver natt |
| **Kontrollpanel** | :3010 | Grønn/gul/rød status med løsninger, innstillinger, oppdateringer, kjøling og loggpakke | ✔ |
| Kvalitetsvakt | – | Bare 1080p eller bedre i Sonarr/Radarr | ✔ kjører hvert 15. min |
| Prefetcharr | – | Henter neste episoder mens du ser | ✔ |
| FlareSolverr, gluetun | – | Cloudflare-hjelper, VPN | ✔ |
| Wizarr, Autobrr | :5690, :7474 | Valgfrie | velges i veiviseren |

**Én innlogging** (brukernavn og passord fra veiviseren) settes på Jellyfin, Seerr, Sonarr, Radarr, Prowlarr, Bazarr, qBittorrent og NZBGet. Med Plex bruker Plex og Seerr Plex-kontoen din.

**Dette gjør du for hånd** (autoconfig minner deg på det til slutt):
1. Legg til indeksere i Prowlarr. De kobles automatisk til Sonarr og Radarr.
2. Velg undertekstkilder i Bazarr (Settings → Providers).
3. Lag brukere til familien i Jellyfin (Kontrollpanel → Brukere), eller inviter dem med Wizarr.
4. Med Plex: sett passord i Tautulli (Settings → Web Interface).

## Jellyfin eller Plex?

| | Jellyfin | Plex |
|---|---|---|
| Pris | Gratis, alt inkludert | Gratis grunnversjon. Maskinvare-transkoding og en del annet krever Plex Pass |
| Kildekode | Åpen (FOSS) | Lukket |
| Konto | Lokale brukere på serveren din | Plex-konto hos plex.tv |
| Apper | Jellyfin for Android, Android TV, iPhone, nettleser. Swiftfin (iPhone, Apple TV), Findroid, Infuse | Plex-appene på nesten alle enheter |
| Tilgang utenfra | Via Tailscale (se under) | Innebygd fjerntilgang |

Du kan bytte senere: kjør veiviseren igjen og velg den andre. Filmene, seriene, Sonarr og Radarr beholdes.

---

## 1. Installer Docker

| System | Slik gjør du det |
|---|---|
| **Linux** (anbefalt) | `./start.sh` tilbyr å installere Docker for deg. For hånd: `curl -fsSL https://get.docker.com \| sh` og `sudo usermod -aG docker $USER`, logg ut og inn igjen |
| **macOS 14 eller nyere** | [OrbStack](https://orbstack.dev) (lettest og raskest) eller [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| **Windows 10/11** | [Docker Desktop](https://www.docker.com/products/docker-desktop/) med WSL 2 (velges under installasjonen) |

**Linux gir best resultat:** mediaserveren får maskinvare-transkoding (Intel/AMD-grafikk). Veiviseren oppdager dette selv. På Mac og Windows kjører Docker i en virtuell maskin, så transkoding skjer bare med prosessoren.

## 2. Kjør veiviseren

Pakk ut zip-fila og start skriptet for systemet ditt (se tabellen øverst), eller kjør `docker compose run --rm setup` i mappen.

Veiviseren spør om:
- **Mediaserver:** Jellyfin (standard) eller Plex.
- **Hvor filmer og serier ligger:**
  1. **På maskinen:** en mappe eller disk, f.eks. `/mnt/media`, `/Volumes/Media` eller `D:/Media`.
  2. **NFS-deling** på en annen maskin (Linux, NAS).
  3. **SMB-deling** (Windows-deling, NAS, Mac-deling).

  Ved nettverksdeling kobler Docker seg til selv, så du slipper å montere noe. Veiviseren tester delingen, viser innholdet og sjekker at den kan skrive dit. Film- og seriemappene finnes automatisk (Filmer/Movies, Serier/TV …), også én mappe ned (f.eks. `Media/movies`).
- **Brukernavn og passord** for appene (et sterkt passord foreslås).
- **IP-adressen** til maskinen på hjemmenettet (foreslås på Linux).
- **VPN-leverandør** og WireGuard-nøkkel. Se [VPN](#vpn).
- **Usenet** (valgfritt): leverandør, brukernavn og passord. Newshosting er forhåndsutfylt.
- **Valgfrie apper** (Wizarr, Autobrr).
- **Rolig modus**: begrenser bakgrunnsappene (anbefalt for bærbare og eldre maskiner). Se [Rolig modus](#rolig-modus).
- **Bare med Plex: Plex-innlogging.** Du får en lenke, logger inn på plex.tv, og resten ordnes automatisk.

Svarene lagres i `.env`. Kjør veiviseren på nytt når som helst. Da blir svarene dine standardvalg.

## 3. Start

```bash
docker compose up -d
docker compose logs -f autoconfig
```

Første gang lastes rundt 3 GB ned, så det tar noen minutter. VPN-en (gluetun) kan bruke et par minutter på å koble til. qBittorrent starter med en gang, men kommer ikke ut på nettet før VPN-en er oppe (killswitch). Deretter bruker **autoconfig** 2–5 minutter på å sette opp alt. Til slutt viser den en oppsummering med adressene og det som eventuelt gjenstår. Oppsummeringen lagres også i `config/OPPSUMMERING.txt`. Trykk `Ctrl+C` for å gå ut av loggen (ingenting stoppes).

Åpne **http://\<maskinen\>:3000** for dashboardet, og **http://\<maskinen\>:8096** for Jellyfin.

---

## Fra Plex-stack til Film-stack

Har du Plex-stack fra før, beholder du alt: filmene, seriene og innstillingene til Sonarr, Radarr, Prowlarr og de andre.

1. Stopp den gamle stacken: `docker compose down` i plex-stack-mappa.
2. Pakk ut Film-stack i en ny mappe, og kopier `.env` og `config/` fra den gamle mappa dit.
3. Kjør veiviseren og velg mediaserver: `docker compose run --rm setup`.
4. Start: `docker compose up -d` og `docker compose logs -f autoconfig`.

Velger du Jellyfin, lages Jellyfin fra bunnen av, og Sonarr og Radarr kobles til Jellyfin i stedet for Plex. Seerr er knyttet til én mediaserver. Var den satt opp mot Plex, sier oppsummeringen hvordan du nullstiller den.

Bruker du Lydbok-stack eller Musikk-stack med nedlasterne herfra, kjører du veiviseren deres på nytt etterpå, så de finner Film-stack.

## Jellyfin på mobil og TV

Last ned **Jellyfin** (Android, Android TV, iPhone) eller **Swiftfin** (iPhone, iPad, Apple TV), og skriv inn `http://<maskinens-ip>:8096`. På hjemmenettet finner appene ofte serveren selv. Smart-TV-er fra LG og Samsung har egne Jellyfin-apper. Utenfor hjemmet bruker du Tailscale.

## VPN

All torrent-trafikk går gjennom VPN-en (gluetun). Faller VPN-en ut, stopper trafikken. Støttet direkte i veiviseren: ProtonVPN, Mullvad, NordVPN, Surfshark, AirVPN og PIA. Gluetun støtter mange flere, se [gluetun-wikien](https://github.com/qdm12/gluetun-wiki).

WireGuard-nøkkel: logg inn hos leverandøren og last ned en WireGuard-konfig. Der står `PrivateKey = …` (og `Address = …`, som Mullvad krever).

Sjekk at VPN-en virker:
```bash
docker exec gluetun wget -qO- https://ipinfo.io     # skal IKKE vise din egen IP
```

## Nettverksdeling (NFS / SMB)

Mediemappa kan ligge på en annen maskin. Velg NFS eller SMB i veiviseren.

**NFS fra en Linux-maskin.** Kjør dette på maskinen som har disken:
```bash
sudo apt install -y nfs-kernel-server
echo '"/sti/til/disken"  192.168.1.XX(rw,sync,no_subtree_check,fsid=1)' | sudo tee -a /etc/exports
sudo exportfs -ra && sudo exportfs -v
```
Bytt ut `192.168.1.XX` med IP-en til serveren. Brukeren som eier mappene, må ha samme bruker-ID på begge maskinene. Sjekk med `id -u` (som regel 1000).

**SMB:** del mappa fra Windows/NAS/Mac med lese- og skrivetilgang for en bruker, og oppgi brukernavn og passord i veiviseren.

**Godt å vite:**
- Maskinen med disken må være på. Er den av, får appene ikke tak i filene før den er på igjen.
- Hardlinks virker over NFS når nedlastinger og bibliotek ligger på samme disk, som de gjør her. Over SMB kopieres filene i stedet.
- Bytter du deling senere: `docker compose down`, kjør veiviseren igjen, og deretter `docker compose up -d`.

## Usenet (NZBGet)

Usenet er ofte raskere og mer stabilt enn torrent, og trenger ikke VPN. Forbindelsen er kryptert (SSL, port 563). Du trenger to ting:

1. **En leverandør** (f.eks. Newshosting). Veiviseren legger den inn i NZBGet. 30 tilkoblinger er et godt utgangspunkt.
2. **Minst én NZB-indekser.** Den finner hva som finnes på Usenet. Populære valg er NZBGeek, NZBFinder og DrunkenSlug (krever invitasjon). To indeksere gir bedre treff enn én.

Legg indekseren inn i **Prowlarr** (*Indexers → Add Indexer*, søk opp navnet og lim inn API-nøkkelen). Prowlarr sender den automatisk videre til Sonarr og Radarr.

## AI-undertekster (Whisper)

Finnes det ingen undertekster på nett, kan [Whisper](https://github.com/ahmetoner/whisper-asr-webservice) lytte på lydsporet og skrive dem. Den slås på i veiviseren, og autoconfig legger den inn som undertekstkilde i Bazarr.

**Hva den kan:** Whisper skriver teksten på språket som snakkes, og kan i tillegg oversette til engelsk. Den kan ikke oversette til norsk. En engelsk film får engelske undertekster, og en norsk film får norske.

**Hvor den kjører** (velges i veiviseren):

| Valg | Fart | Krever |
| --- | --- | --- |
| Denne maskinen, prosessoren | Tregt. En film kan ta like lang tid som den varer, eller lenger på eldre maskiner | Ingenting |
| Denne maskinen, NVIDIA-skjermkort | Raskt, minutter per film | NVIDIA-driver og [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) |
| En annen maskin på nettverket | Raskt med skjermkort | Kopier mappa `whisper-ekstern` dit og kjør `docker compose up -d` |

**Modeller:** `small` er raskest og grei på engelsk, men svakere på norsk. `turbo` er god på begge og anbefales. `large-v3` er best, men tregest. Modellen lastes ned første gang den brukes (0,5–3 GB) og slippes fra minnet etter fem minutter uten jobb.

**Bruk:** I Bazarr åpner du en film eller episode, trykker på søk (forstørrelsesglasset) og velger treffet fra *whisperai*. Teksten lages i bakgrunnen.

**Automatisk** (valgfritt i veiviseren): Whisper-tekster får alltid en fast, lav poengsum i Bazarr (61 % for episoder, 33 % for filmer). For at de skal hentes av seg selv, senkes kravet til poengsum, og det gjelder alle kilder. Da kan Bazarr også godta dårligere treff fra vanlige kilder, og en maskin uten skjermkort kan bruke dager på et stort bibliotek. Derfor er det av som standard.

## Rolig modus

Slås på i veiviseren (`compose.quiet.yaml`). Bakgrunnsappene (Sonarr, Radarr, NZBGet, qBittorrent, Bazarr …) får en grense for hvor mye prosessor de kan bruke, og taper alltid mot mediaserveren når maskinen er travel. **Jellyfin og Plex begrenses aldri**, så avspillingen påvirkes ikke. NZBGet pakker også ut én nedlasting om gangen.

Er vifta fortsatt mye i gang: skru av forhåndsvisningsbilder (*trickplay* i Jellyfin, *forhåndsvisningsbilder* i Plex), eller la dem bare lages om natta. På Linux kan du også skru av Turbo Boost: `echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo`.

## Kvalitet – bare 1080p eller bedre

- **Recyclarr** legger inn TRaSH Guides sine profiler: *WEB-1080p* for serier og *HD Bluray + WEB* for filmer.
- **Kvalitetsvakten** slår av alt under 1080p (og plate-kopier) i alle profiler. Profiler som da blir tomme, slettes, og seriene og filmene som brukte dem, flyttes til TRaSH-profilen. Vakten kjører hvert 15. minutt, fordi Recyclarr setter guidens kvaliteter tilbake hver natt.
- **Har du filer under 1080p**, oppgraderer Sonarr og Radarr dem når en 1080p-utgave dukker opp.
- **Fjernede utgivelser:** NZBGet avbryter med en gang når en utgivelse mangler biter, og Sonarr og Radarr prøver automatisk neste.

Grensen settes med `MIN_RESOLUTION` i `.env` (standard `1080`). For 4K: sett `RECYCLARR_SONARR_TEMPLATE='web-2160p'` og `RECYCLARR_RADARR_TEMPLATE='uhd-bluray-web'`, slett de gamle malene i `config/recyclarr/configs/`, og kjør autoconfig igjen.

## Kontrollpanel

`http://<maskinen>:3010`, eller kortet **Kontrollpanel** på dashboardet. Logg inn med brukernavnet og passordet fra veiviseren.

Panelet sjekker hele stacken hvert kvarter, med de samme testene som `docker compose run --rm sjekk`, og viser alt som grønt, gult eller rødt med en forklaring. Der det finnes en løsning, står den på en knapp.

**Rettes av seg selv** (kan slås av øverst i panelet): en app som har stoppet eller er syk, startes på nytt. En VPN som ikke kommer ut, startes på nytt. Lekker torrent-trafikken utenom VPN-en, stoppes qBittorrent, og den blir stående til du starter den igjen. Manglende tjenester startes. Hver løsning prøves høyst to ganger per seks timer. Hjelper det ikke, ber panelet om hjelp i stedet for å prøve i det uendelige. De første fem minuttene etter en omstart gjør panelet ingenting av seg selv, fordi appene fortsatt starter.

**Bare når du trykker:** autooppsettet, ny test av indekserne, og alt som endrer innstillinger.

| Seksjon | Hva du kan gjøre |
| --- | --- |
| Kvalitet | Velge laveste oppløsning: 720p, 1080p eller bare 4K |
| Disk | Se ledig plass og hvor mediemappa ligger (lokal disk, NFS eller SMB). Velge når du skal varsles, og når nedlastingene skal settes på pause så disken aldri blir full |
| Fart | Fartsgrenser for nedlasting, vanlige og rolige, og et tidsrom hver dag med rolige grenser |
| Oppdateringer | Oppdatere alle appene hver natt, en gang i uka eller aldri, og vente hvis noen ser på noe. Sjekken kjøres etterpå. «Oppdater nå»-knapp. Virker på Linux og Mac, ikke Windows |
| Indeksere | Se hvilke indeksere som svarer, er satt på pause eller feiler, med søk, treff og svartid. Teste og slå av eller på |
| Kjøling | Temperatur, prosessor og minne, og knappene Automatisk, Alltid rolig og Aldri rolig |
| Feilsøking | Debug-logging av og på, loggpakke og autooppsettet |

Innstillingene lagres i `config/panel/innstillinger.json` og legges rett inn i appene. Det du velger i panelet, gjelder foran `MIN_RESOLUTION` i `.env`.

### Logger til feilsøking

1. **Feilsøking → Slå på i 24 timer.** Sonarr, Radarr, Prowlarr, Bazarr og Jellyfin eller Plex logger mye mer. Det slås av av seg selv etter 24 timer.
2. Gjenta det som feiler.
3. **Last ned logger.** Du får en zip med det alle tjenestene har skrevet, appenes egne loggfiler, siste sjekk, containerne og maskininfo.

Passord, API-nøkler, token, e-postadresser og offentlige IP-adresser er byttet ut med `[SLADDET]`, `[NØKKEL]`, `[TOKEN]`, `[E-POST]` og `offentlig-IP-1`. Adresser på hjemmenettet, som 192.168.x.x, står igjen, fordi de trengs til feilsøking. Se likevel gjennom zip-fila før du deler den.

## Kjøling – belastning og automatisk nedkjøling

Ligger i kontrollpanelet under **Kjøling**. Tre valg: **Automatisk** (rolig over 85 °C eller 90 % prosessor i 2 minutter), **Alltid rolig** og **Aldri rolig**.

I rolig modus får bakgrunnsappene maks 0,3 prosessortråd hver, qBittorrent bytter til lave fartsgrenser, og NZBGet venter med utpakking. Med Plex stoppes også planlagte vedlikeholdsjobber. **Mediaserveren begrenses aldri.** Grensene står i `.env` (`HOT_TEMP`, `COOL_TEMP`, `HIGH_LOAD`, `CALM_CPUS`).

## Sjekk at alt virker

```bash
docker compose run --rm sjekk
```

Startskriptet kjører denne av seg selv når oppsettet er ferdig. Den tester at ting faktisk virker, ikke bare at de kjører: at torrent-trafikken går gjennom VPN-en og ikke lekker, at Usenet-innloggingen virker, at indekserne svarer, at nedlasterne og bibliotekmappene er nåbare, at hardlinks virker på disken, og at mediaserveren kan logges inn i og har bibliotekene sine. Hver linje får ✔, ⚠ eller ✘ med et forslag til hva du gjør. Resultatet lagres i `config/SJEKK.txt`.

## Kjøre oppsettet på nytt

```bash
docker compose run --rm autoconfig
```
Det er trygt å kjøre igjen. Den sjekker hva som allerede er gjort og retter bare avvik.

## Oppdatere

**Appene** oppdateres automatisk fra kontrollpanelet (søndag kl. 04 som standard), eller for hånd med `docker compose pull && docker compose up -d`, eller `oppdater.sh` / `Oppdater.command` / `Oppdater.bat`.
**Film-stack selv:** last ned nyeste pakke og pakk den ut over den gamle mappa. `.env` og `config/` blir ikke overskrevet. Kjør deretter `docker compose up -d` og `docker compose run --rm autoconfig`.

## Tilgang utenfra med Tailscale

Installer Tailscale på serveren og på telefonen/PC-en du vil bruke ute:
- **Linux:** `curl -fsSL https://tailscale.com/install.sh | sh` og `sudo tailscale up`
- **Mac/Windows:** appen fra https://tailscale.com/download

I [Tailscale-admin](https://login.tailscale.com/admin): slå på **MagicDNS**, og **Disable key expiry** for serveren. Kjør veiviseren igjen, og skriv Tailscale-navnet (f.eks. `filmserver.tail1234.ts.net`) som «navn du bruker i nettleseren». I Jellyfin-appen bruker du samme navn med `:8096`.

## Hverdagsbruk

```bash
docker compose ps                              # status
docker compose logs -f sonarr                  # logg for én app
docker compose pull && docker compose up -d    # oppdater alle apper
```
**Backup:** ta kopi av `config/`-mappa og `.env`.

## Plattformnotater

- **Linux:** maskinvare-transkoding via `/dev/dri` når det finnes. Plex bruker vertens nettverk. Skriptene i `linux/` (bare i Linux-pakken) hjelper med server-oppsett: `01-prepare-host.sh` (Docker, Tailscale, ingen dvale), `02-mount-disk.sh`, `02-mount-nfs.sh`, `03-format-ext4.sh` og `05-move-to-new-disk.sh`.
- **macOS:** skru av dvale (Systeminnstillinger → Strøm). Eksterne disker må ha tilgang under Personvern og sikkerhet → Filer og mapper. Docker Desktop/OrbStack må starte ved innlogging.
- **Windows:** bruk stier som `D:/Media` (skråstrek fremover). Skru av dvale. Docker Desktop må starte ved innlogging.

## Feilsøking

- **Først:** åpne kontrollpanelet (`:3010`). Det viser hva som er galt og har knapper for det meste. Trenger du hjelp fra andre, slå på debug, gjenta feilen og send loggpakken.
- **Autoconfig viser ⚠:** les meldingen, rett det som mangler, og kjør `docker compose run --rm autoconfig` igjen.
- **qBittorrent/gluetun starter ikke:** `docker logs gluetun`. Nesten alltid feil eller manglende VPN-nøkkel i `.env`.
- **«qBittorrent hoppet over» i Sonarr/Radarr:** VPN-en var ikke oppe da autoconfig kjørte. Når `docker compose ps gluetun` viser `healthy`, kjører du autoconfig igjen.
- **Jellyfin: innlogging feilet:** passordet er endret i Jellyfin. Sett det tilbake, eller rett `ADMIN_PASSWORD` i `.env` og kjør autoconfig.
- **Jellyfin-appen finner ikke serveren:** skriv inn `http://<maskinens-ip>:8096` selv.
- **Plex ble ikke klar / ikke knyttet til kontoen:** vent litt og kjør autoconfig igjen. Hjelper ikke det, kjør veiviseren og logg inn i Plex på nytt.
- **Tomme biblioteker:** sjekk `DATA_ROOT` i `.env`. `docker compose run --rm init` viser hvilke mapper som ble funnet.
- **«Host validation failed» på dashboardet:** adressen du bruker mangler i `HOMEPAGE_ALLOWED_HOSTS` i `.env`. Kjør veiviseren igjen.
- **Indekser blokkert av Cloudflare:** FlareSolverr klarer ikke alle sider. Bruk andre indeksere.

## Ansvar

Film-stack setter opp programvare. Du er selv ansvarlig for hva du laster ned, og for å følge lovene der du bor. Bruk VPN for torrent.

## Takk til

[Jellyfin](https://jellyfin.org) · [Plex](https://www.plex.tv) · [Sonarr](https://sonarr.tv) · [Radarr](https://radarr.video) · [Prowlarr](https://prowlarr.com) · [Bazarr](https://www.bazarr.media) · [Whisper ASR Webservice](https://github.com/ahmetoner/whisper-asr-webservice) · [Seerr](https://github.com/seerr-team/seerr) · [Tautulli](https://tautulli.com) · [qBittorrent](https://www.qbittorrent.org) · [NZBGet](https://nzbget.com) · [gluetun](https://github.com/qdm12/gluetun) · [Recyclarr](https://recyclarr.dev) · [TRaSH Guides](https://trash-guides.info) · [Homepage](https://gethomepage.dev) · [Prefetcharr](https://github.com/p-hueber/prefetcharr) · [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) · [LinuxServer.io](https://www.linuxserver.io)

Lisens: MIT (se `LICENSE`).
