# Film-stack – an automatic media server on Docker

[Norsk](README.md) · **English** · [Website](https://munkenbissniss.github.io/film-stack/en/) · [All projects](https://munkenbissniss.github.io/en/)

**One wizard, one command, and everything is set up.** Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent behind a VPN, NZBGet, Bazarr, Seerr and a dashboard run in Docker on **Linux, macOS and Windows**. The apps are configured and connected automatically through their APIs, so you don't have to click through twenty settings pages.

**Jellyfin** is the default: free, open source, and everything is included, hardware transcoding too. If you prefer **Plex**, choose it in the wizard.

> Formerly Plex-stack. Inspired by [DonMcD/ultimate-plex-stack](https://github.com/DonMcD/ultimate-plex-stack). *Film* means movies in Norwegian.

The wizard and the setup messages are in Norwegian. The questions are short, and this README explains each of them. In the wizard, `j` means yes and `n` means no.

## Download

| System | Package | Start with |
|---|---|---|
| 🐧 **Linux** (recommended) | [film-stack-linux.zip](../../releases/latest/download/film-stack-linux.zip) | `./start.sh` |
| 🍎 **macOS 14+** | [film-stack-macos.zip](../../releases/latest/download/film-stack-macos.zip) | double-click `Start.command` |
| 🪟 **Windows 10/11** | [film-stack-windows.zip](../../releases/latest/download/film-stack-windows.zip) | double-click `Start.bat` |

All versions are under [Releases](../../releases). The start script runs the wizard the first time, starts everything and shows the setup as it happens. You can also do it by hand:

```
docker compose run --rm setup      # 1. answer a few questions (2 min)
docker compose up -d               # 2. start everything
docker compose logs -f autoconfig  # 3. watch everything set itself up
```

## What you get

| App | Address | Does | Set up automatically |
|---|---|---|---|
| **Homepage** | :3000 | Dashboard with everything | ✔ all widgets and keys |
| **Jellyfin** | :8096 | Media server (default) | ✔ admin, Movies and Shows libraries, metadata language, hardware transcoding |
| Plex | :32400/web | Media server (optional instead of Jellyfin) | ✔ linked to your account, libraries created |
| Sonarr / Radarr | :8989 / :7878 | Shows / movies | ✔ login, root folders, downloaders, media server refresh, hardlinks |
| Prowlarr | :9696 | Indexers | ✔ login, FlareSolverr, connected to Sonarr/Radarr |
| qBittorrent | :8080 | Torrents behind VPN | ✔ password, folders, categories |
| NZBGet | :6789 | Usenet (optional) | ✔ server, password, folders, categories |
| Bazarr | :6767 | Subtitles | ✔ Sonarr/Radarr, language profile (Norwegian + English) |
| Whisper | :9000 | AI subtitles when none exist (optional) | ✔ added to Bazarr |
| Seerr | :5055 | Requests for family and friends | ✔ sign-in with Jellyfin or Plex, Sonarr/Radarr, libraries |
| Tautulli | :8181 | Who is watching what (Plex only) | ✔ Plex |
| Recyclarr | – | TRaSH Guides quality profiles | ✔ syncs every night |
| **Control panel** | :3010 | Green/yellow/red status with fixes, settings, updates, cooling and log bundle | ✔ |
| Quality guard | – | Only 1080p or better in Sonarr/Radarr | ✔ runs every 15 min |
| Prefetcharr | – | Fetches the next episodes while you watch | ✔ |
| FlareSolverr, gluetun | – | Cloudflare helper, VPN | ✔ |
| Wizarr, Autobrr | :5690, :7474 | Optional | chosen in the wizard |

**One login** (username and password from the wizard) is set on Jellyfin, Seerr, Sonarr, Radarr, Prowlarr, Bazarr, qBittorrent and NZBGet. With Plex, Plex and Seerr use your Plex account.

Libraries are named *Filmer* (movies) and *Serier* (shows), and metadata is fetched in Norwegian. For English metadata, set `METADATA_LANGUAGE='en'` and `METADATA_COUNTRY='US'` in `.env` before the first start, and `SUBTITLE_LANGS='en'` for English subtitles only.

**What you do by hand** (autoconfig reminds you at the end):
1. Add indexers in Prowlarr. They are connected to Sonarr and Radarr automatically.
2. Pick subtitle providers in Bazarr (Settings → Providers).
3. Create users for the family in Jellyfin (Dashboard → Users), or invite them with Wizarr.
4. With Plex: set a password in Tautulli (Settings → Web Interface).

## Jellyfin or Plex?

| | Jellyfin | Plex |
|---|---|---|
| Price | Free, everything included | Free basic tier. Hardware transcoding and some other features need Plex Pass |
| Source code | Open (FOSS) | Closed |
| Accounts | Local users on your server | A Plex account at plex.tv |
| Apps | Jellyfin for Android, Android TV, iPhone, browser. Swiftfin (iPhone, Apple TV), Findroid, Infuse | Plex apps on almost every device |
| Remote access | Through Tailscale (see below) | Built in |

You can switch later: run the wizard again and choose the other one. Your movies, shows, Sonarr and Radarr are kept.

---

## 1. Install Docker

| System | How |
|---|---|
| **Linux** (recommended) | `./start.sh` offers to install Docker for you. By hand: `curl -fsSL https://get.docker.com \| sh` and `sudo usermod -aG docker $USER`, then log out and back in |
| **macOS 14 or newer** | [OrbStack](https://orbstack.dev) (lightest and fastest) or [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| **Windows 10/11** | [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL 2 (chosen during installation) |

**Linux gives the best result:** the media server gets hardware transcoding (Intel/AMD graphics). The wizard detects this by itself. On Mac and Windows, Docker runs in a virtual machine, so transcoding uses the CPU only.

## 2. Run the wizard

Unzip the file and start the script for your system (see the table at the top), or run `docker compose run --rm setup` in the folder.

The wizard asks for:
- **Media server** (*Mediaserver*): Jellyfin (default) or Plex.
- **Where your movies and shows are:**
  1. **On this machine:** a folder or disk, for example `/mnt/media`, `/Volumes/Media` or `D:/Media`.
  2. **An NFS share** on another machine (Linux, NAS).
  3. **An SMB share** (Windows share, NAS, Mac share).

  With a network share, Docker connects by itself. The wizard tests the share, shows its contents and checks that it can write there. The movie and show folders are found automatically (Filmer/Movies, Serier/TV …), also one level down (for example `Media/movies`).
- **Username and password** for the apps (a strong password is suggested).
- **The IP address** of the machine on your home network (suggested on Linux).
- **VPN provider** and WireGuard key. See [VPN](#vpn).
- **Usenet** (optional): provider, username and password. Newshosting is filled in.
- **Optional apps** (Wizarr, Autobrr).
- **Quiet mode** (*rolig modus*): limits the background apps (recommended for laptops and older machines).
- **Plex only: Plex sign-in.** You get a link, sign in at plex.tv, and the rest is handled automatically.

The answers are saved in `.env`. Run the wizard again at any time, and your answers become the defaults.

## 3. Start

```bash
docker compose up -d
docker compose logs -f autoconfig
```

The first time, about 3 GB is downloaded, so it takes a few minutes. The VPN (gluetun) can take a couple of minutes to connect. qBittorrent starts right away, but can't reach the internet until the VPN is up (killswitch). Then **autoconfig** spends 2–5 minutes setting everything up. At the end it shows a summary with the addresses and anything left to do. The summary is also saved in `config/OPPSUMMERING.txt`. Press `Ctrl+C` to leave the log (nothing is stopped).

Open **http://\<machine\>:3000** for the dashboard, and **http://\<machine\>:8096** for Jellyfin.

---

## From Plex-stack to Film-stack

If you already run Plex-stack, you keep everything: your movies, shows and the settings of Sonarr, Radarr, Prowlarr and the rest.

1. Stop the old stack: `docker compose down` in the plex-stack folder.
2. Unzip Film-stack in a new folder, and copy `.env` and `config/` from the old folder into it.
3. Run the wizard and choose a media server: `docker compose run --rm setup`.
4. Start: `docker compose up -d` and `docker compose logs -f autoconfig`.

If you choose Jellyfin, Jellyfin is set up from scratch, and Sonarr and Radarr are connected to Jellyfin instead of Plex. Seerr is tied to one media server. If it was set up for Plex, the summary explains how to reset it.

If you use Lydbok-stack or Musikk-stack with the downloaders from here, run their wizards again afterwards so they find Film-stack.

## Jellyfin on phone and TV

Install **Jellyfin** (Android, Android TV, iPhone) or **Swiftfin** (iPhone, iPad, Apple TV), and enter `http://<machine-ip>:8096`. On the home network, the apps often find the server by themselves. LG and Samsung smart TVs have their own Jellyfin apps. Away from home, use Tailscale.

## VPN

All torrent traffic goes through the VPN (gluetun). If the VPN drops, traffic stops. Supported directly in the wizard: ProtonVPN, Mullvad, NordVPN, Surfshark, AirVPN and PIA. Gluetun supports many more, see the [gluetun wiki](https://github.com/qdm12/gluetun-wiki).

WireGuard key: sign in at your provider and download a WireGuard config. It contains `PrivateKey = …` (and `Address = …`, which Mullvad requires).

Check that the VPN works:
```bash
docker exec gluetun wget -qO- https://ipinfo.io     # should NOT show your own IP
```

## Network share (NFS / SMB)

The media folder can live on another machine. Choose NFS or SMB in the wizard.

**NFS from a Linux machine.** Run this on the machine that has the disk:
```bash
sudo apt install -y nfs-kernel-server
echo '"/path/to/disk"  192.168.1.XX(rw,sync,no_subtree_check,fsid=1)' | sudo tee -a /etc/exports
sudo exportfs -ra && sudo exportfs -v
```
Replace `192.168.1.XX` with the IP of the server. The user who owns the folders must have the same user ID on both machines. Check with `id -u` (usually 1000).

**SMB:** share the folder from Windows/NAS/Mac with read and write access for a user, and enter the username and password in the wizard.

**Good to know:**
- The machine with the disk must be on. If it is off, the apps can't reach the files until it is back.
- Hardlinks work over NFS when downloads and library are on the same disk, as they are here. Over SMB, files are copied instead.
- To switch shares later: `docker compose down`, run the wizard again, then `docker compose up -d`.

## Usenet (NZBGet)

Usenet is often faster and more stable than torrents, and doesn't need a VPN. The connection is encrypted (SSL, port 563). You need two things:

1. **A provider** (for example Newshosting). The wizard adds it to NZBGet. 30 connections is a good starting point.
2. **At least one NZB indexer.** It finds what exists on Usenet. Popular choices are NZBGeek, NZBFinder and DrunkenSlug (invite only). Two indexers find more than one.

Add the indexer in **Prowlarr** (*Indexers → Add Indexer*, search for the name and paste the API key). Prowlarr passes it to Sonarr and Radarr automatically.

## AI subtitles (Whisper)

When no subtitles exist online, [Whisper](https://github.com/ahmetoner/whisper-asr-webservice) can listen to the audio track and write them. Turn it on in the wizard, and autoconfig adds it as a subtitle provider in Bazarr.

**What it can do:** Whisper writes the text in the spoken language, and can also translate to English. It cannot translate to Norwegian. An English film gets English subtitles, and a Norwegian film gets Norwegian ones.

**Where it runs** (chosen in the wizard):

| Choice | Speed | Needs |
| --- | --- | --- |
| This machine, CPU | Slow. A film can take as long as it runs, or longer on older machines | Nothing |
| This machine, NVIDIA GPU | Fast, minutes per film | NVIDIA driver and [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) |
| Another machine on the network | Fast with a GPU | Copy the `whisper-ekstern` folder there and run `docker compose up -d` |

**Models:** `small` is fastest and fine for English, but weaker for Norwegian. `turbo` is good at both and recommended. `large-v3` is best, but slowest. The model downloads the first time it is used (0.5–3 GB) and is unloaded after five idle minutes.

**Use:** In Bazarr, open a film or episode, click search (the magnifying glass) and pick the result from *whisperai*. The text is made in the background.

**Automatic** (optional in the wizard): Whisper subtitles always get a fixed, low score in Bazarr (61% for episodes, 33% for films). To fetch them on their own, the minimum score is lowered, and that applies to every provider. Bazarr may then also accept weaker matches from normal providers, and a machine without a GPU can spend days on a large library. That's why it's off by default.

## Quiet mode

Turned on in the wizard (`compose.quiet.yaml`). The background apps (Sonarr, Radarr, NZBGet, qBittorrent, Bazarr …) get a limit on how much CPU they can use, and always lose to the media server when the machine is busy. **Jellyfin and Plex are never limited**, so playback is not affected. NZBGet also unpacks one download at a time.

If the fan still runs a lot: turn off preview thumbnails (*trickplay* in Jellyfin, *preview thumbnails* in Plex), or only generate them at night. On Linux you can also turn off Turbo Boost: `echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo`.

## Quality – 1080p or better only

- **Recyclarr** adds the TRaSH Guides profiles: *WEB-1080p* for shows and *HD Bluray + WEB* for movies.
- **The quality guard** turns off everything below 1080p (and disc rips) in all profiles. Profiles that become empty are deleted, and the shows and movies that used them move to the TRaSH profile. The guard runs every 15 minutes, because Recyclarr resets the guide's qualities every night.
- **If you have files below 1080p**, Sonarr and Radarr upgrade them when a 1080p release shows up.
- **Removed releases:** NZBGet stops at once when a release is missing parts, and Sonarr and Radarr try the next one automatically.

The limit is set with `MIN_RESOLUTION` in `.env` (default `1080`). For 4K: set `RECYCLARR_SONARR_TEMPLATE='web-2160p'` and `RECYCLARR_RADARR_TEMPLATE='uhd-bluray-web'`, delete the old templates in `config/recyclarr/configs/`, and run autoconfig again.

## Control panel

`http://<machine>:3010`, or the **Kontrollpanel** card on the dashboard. Log in with the username and password from the wizard.

The panel checks the whole stack every 15 minutes, with the same tests as `docker compose run --rm sjekk`, and shows everything as green, yellow or red with an explanation. Where there's a fix, it's on a button.

**Fixed on its own** (can be turned off at the top of the panel): an app that has stopped or is unhealthy is restarted. A VPN that can't get out is restarted. If torrent traffic leaks outside the VPN, qBittorrent is stopped and stays stopped until you start it. Missing services are started. Each fix is tried at most twice per six hours. If that doesn't help, the panel asks for help instead of trying forever. For the first five minutes after a restart the panel does nothing on its own, because the apps are still starting.

**Only when you press:** autoconfig, retesting indexers, and anything that changes settings.

| Section | What you can do |
| --- | --- |
| Kvalitet (quality) | Pick the lowest resolution: 720p, 1080p or 4K only |
| Disk | See free space and where the media folder lives (local disk, NFS or SMB). Choose when to warn, and when downloads pause so the disk never fills up |
| Fart (speed) | Download speed limits, normal and quiet, and a daily time window with quiet limits |
| Oppdateringer (updates) | Update all apps every night, once a week or never, and wait if someone is watching. The check runs afterwards. "Update now" button. Works on Linux and Mac, not Windows |
| Indeksere (indexers) | See which indexers respond, are paused or failing, with searches, grabs and response time. Test and turn on or off |
| Kjøling (cooling) | Temperature, CPU and memory, and the Automatic, Always quiet and Never quiet buttons |
| Feilsøking (troubleshooting) | Debug logging on and off, log bundle and autoconfig |

Settings are saved in `config/panel/innstillinger.json` and applied straight to the apps. What you choose in the panel takes precedence over `MIN_RESOLUTION` in `.env`.

### Logs for troubleshooting

1. **Feilsøking → Slå på i 24 timer** (turn on for 24 hours). Sonarr, Radarr, Prowlarr, Bazarr and Jellyfin or Plex log much more. It turns itself off after 24 hours.
2. Repeat what fails.
3. **Last ned logger** (download logs). You get a zip with what every service has written, the apps' own log files, the latest check, the containers and machine info.

Passwords, API keys, tokens, email addresses and public IP addresses are replaced with `[SLADDET]`, `[NØKKEL]`, `[TOKEN]`, `[E-POST]` and `offentlig-IP-1`. Home network addresses like 192.168.x.x are kept, because they're needed for troubleshooting. Still look through the zip before you share it.

## Cooling – load and automatic calming

Lives in the control panel under **Kjøling**. Three choices: **Automatisk** (automatic: quiet above 85 °C or 90 % CPU for 2 minutes), **Alltid rolig** (always quiet) and **Aldri rolig** (never quiet).

In quiet mode, the background apps get at most 0.3 CPU thread each, qBittorrent switches to low speed limits, and NZBGet waits with unpacking. With Plex, scheduled maintenance jobs are also paused. **The media server is never limited.** The limits are in `.env` (`HOT_TEMP`, `COOL_TEMP`, `HIGH_LOAD`, `CALM_CPUS`).

## Check that everything works

```bash
docker compose run --rm sjekk
```

The start script runs this by itself when the setup has finished. It tests that things actually work, not just that they are running: that torrent traffic goes through the VPN and doesn't leak, that the Usenet login works, that the indexers respond, that the downloaders and library folders are reachable, that hardlinks work on the disk, and that you can sign in to the media server and its libraries exist. Each line gets ✔, ⚠ or ✘ with a suggestion for what to do. The result is saved in `config/SJEKK.txt`.

## Running the setup again

```bash
docker compose run --rm autoconfig
```
It is safe to run again. It checks what is already done and only fixes what differs.

## Updating

**The apps** are updated automatically from the control panel (Sunday at 04:00 by default), or by hand with `docker compose pull && docker compose up -d`, or `oppdater.sh` / `Oppdater.command` / `Oppdater.bat`.
**Film-stack itself:** download the latest package and unzip it over the old folder. `.env` and `config/` are not overwritten. Then run `docker compose up -d` and `docker compose run --rm autoconfig`.

## Remote access with Tailscale

Install Tailscale on the server and on the phone or computer you want to use away from home:
- **Linux:** `curl -fsSL https://tailscale.com/install.sh | sh` and `sudo tailscale up`
- **Mac/Windows:** the app from https://tailscale.com/download

In the [Tailscale admin](https://login.tailscale.com/admin): turn on **MagicDNS**, and **Disable key expiry** for the server. Run the wizard again, and enter the Tailscale name (for example `filmserver.tail1234.ts.net`) as the name you use in the browser. In the Jellyfin app, use the same name with `:8096`.

## Everyday use

```bash
docker compose ps                              # status
docker compose logs -f sonarr                  # log for one app
docker compose pull && docker compose up -d    # update all apps
```
**Backup:** copy the `config/` folder and `.env`.

## Platform notes

- **Linux:** hardware transcoding through `/dev/dri` when it exists. Plex uses the host network. The scripts in `linux/` (Linux package only) help with server setup: `01-prepare-host.sh` (Docker, Tailscale, no sleep), `02-mount-disk.sh`, `02-mount-nfs.sh`, `03-format-ext4.sh` and `05-move-to-new-disk.sh`.
- **macOS:** turn off sleep (System Settings → Energy). External disks need access under Privacy & Security → Files and Folders. Docker Desktop/OrbStack must start at login.
- **Windows:** use paths like `D:/Media` (forward slash). Turn off sleep. Docker Desktop must start at login.

## Troubleshooting

- **Autoconfig shows ⚠:** read the message, fix what is missing, and run `docker compose run --rm autoconfig` again.
- **qBittorrent/gluetun doesn't start:** `docker logs gluetun`. Almost always a wrong or missing VPN key in `.env`.
- **"qBittorrent hoppet over" in Sonarr/Radarr:** the VPN wasn't up when autoconfig ran. When `docker compose ps gluetun` shows `healthy`, run autoconfig again.
- **Jellyfin: login failed:** the password was changed in Jellyfin. Change it back, or fix `ADMIN_PASSWORD` in `.env` and run autoconfig.
- **The Jellyfin app can't find the server:** enter `http://<machine-ip>:8096` yourself.
- **Plex wasn't ready / not linked to your account:** wait a bit and run autoconfig again. If that doesn't help, run the wizard, sign in to Plex again, and then autoconfig.
- **Empty libraries:** check `DATA_ROOT` in `.env`. `docker compose run --rm init` shows which folders were found.
- **"Host validation failed" on the dashboard:** the address you use is missing from `HOMEPAGE_ALLOWED_HOSTS` in `.env`. Run the wizard again.
- **Indexer blocked by Cloudflare:** FlareSolverr can't get through every site. Use other indexers.

## Responsibility

Film-stack sets up software. You are responsible for what you download and for following the laws where you live. Use a VPN for torrents.

## Thanks to

[Jellyfin](https://jellyfin.org) · [Plex](https://www.plex.tv) · [Sonarr](https://sonarr.tv) · [Radarr](https://radarr.video) · [Prowlarr](https://prowlarr.com) · [Bazarr](https://www.bazarr.media) · [Whisper ASR Webservice](https://github.com/ahmetoner/whisper-asr-webservice) · [Seerr](https://github.com/seerr-team/seerr) · [Tautulli](https://tautulli.com) · [qBittorrent](https://www.qbittorrent.org) · [NZBGet](https://nzbget.com) · [gluetun](https://github.com/qdm12/gluetun) · [Recyclarr](https://recyclarr.dev) · [TRaSH Guides](https://trash-guides.info) · [Homepage](https://gethomepage.dev) · [Prefetcharr](https://github.com/p-hueber/prefetcharr) · [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) · [LinuxServer.io](https://www.linuxserver.io)

License: MIT (see `LICENSE`).
