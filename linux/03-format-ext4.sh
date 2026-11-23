#!/bin/bash
# VALGFRITT og DESTRUKTIVT: formaterer en disk som ext4 (beste valg på Linux).
# Bruk bare på en TOM disk, eller etter at innholdet er kopiert et annet sted.
# Kjør:  sudo ./linux/03-format-ext4.sh
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo "Kjør med sudo: sudo $0" >&2; exit 1; }

lsblk -o NAME,SIZE,FSTYPE,LABEL,MODEL,MOUNTPOINTS
echo
read -r -p "Hvilken HEL disk skal formateres? (f.eks. sdb – IKKE sda): " D
DISK="/dev/${D#/dev/}"
[ -b "$DISK" ] || { echo "Finner ikke $DISK" >&2; exit 1; }
ROOTDISK=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)")
[ "${DISK#/dev/}" = "$ROOTDISK" ] && { echo "Det er systemdisken! Avbryter." >&2; exit 1; }

echo
lsblk "$DISK"
echo "ALT på $DISK slettes."
read -r -p "Skriv SLETT for å fortsette: " OK
[ "$OK" = "SLETT" ] || { echo "Avbrutt."; exit 1; }

umount "${DISK}"* 2>/dev/null || true
wipefs -a "$DISK"
parted -s "$DISK" mklabel gpt mkpart media ext4 0% 100%
sleep 2
PART=$(lsblk -lnpo NAME "$DISK" | sed -n 2p)
mkfs.ext4 -F -L media -m 0 "$PART"
echo "Ferdig: $PART er formatert som ext4. Kjør nå: sudo ./linux/02-mount-disk.sh"
