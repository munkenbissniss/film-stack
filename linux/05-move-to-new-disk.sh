#!/bin/bash
# Flytter alt fra den midlertidige lagringen (NFS-deling eller USB-pinne på
# /mnt/media) over til den nye disken, og gjør den nye disken til /mnt/media.
# Stiene inne i appene (/data/...) blir de samme, så ingenting må konfigureres på nytt.
#
# Forutsetter at den nye disken er formatert: sudo ./linux/03-format-ext4.sh
# Kjør:  sudo ./linux/05-move-to-new-disk.sh
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo "Kjør med sudo: sudo $0" >&2; exit 1; }
cd "$(dirname "$0")/.."
USER_NAME="${SUDO_USER:-$(logname)}"
NEW=/mnt/newdisk

mountpoint -q /mnt/media || { echo "FEIL: /mnt/media er ikke montert." >&2; exit 1; }
lsblk -o NAME,SIZE,FSTYPE,LABEL,MODEL,MOUNTPOINTS
read -r -p "Partisjonen på den NYE disken (f.eks. sdb1): " P
DEV="/dev/${P#/dev/}"
[ "$(blkid -s TYPE -o value "$DEV")" = "ext4" ] || { echo "FEIL: $DEV er ikke ext4. Kjør 03-format-ext4.sh først." >&2; exit 1; }

mkdir -p "$NEW"
mountpoint -q "$NEW" || mount "$DEV" "$NEW"
chown "$(id -u "$USER_NAME"):$(id -g "$USER_NAME")" "$NEW"

echo "Plass brukt nå: $(du -sh /mnt/media 2>/dev/null | cut -f1)   Ledig på ny disk: $(df -h --output=avail "$NEW" | tail -1)"
read -r -p "Starte kopieringen? Stacken kan kjøre så lenge. [j/N] " OK
[[ "$OK" =~ ^[jJyY]$ ]] || exit 0

echo "== Runde 1: kopierer (kan ta mange timer for store bibliotek) =="
rsync -aH --info=progress2 /mnt/media/ "$NEW"/

echo "== Stopper stacken og tar siste endringer =="
sudo -u "$USER_NAME" docker compose down
rsync -aH --delete --info=progress2 /mnt/media/ "$NEW"/
umount "$NEW"

echo "== Gjør den nye disken til /mnt/media =="
echo "Velg $P igjen når du blir spurt."
./linux/02-mount-disk.sh

sudo -u "$USER_NAME" docker compose up -d
echo
echo "Ferdig! Alt kjører nå fra den nye disken."
echo "Den gamle lagringen er ikke slettet. Kontroller at alt virker før du rydder der."
