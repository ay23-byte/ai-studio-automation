#!/usr/bin/env bash
# Resilient package installation for GitHub-hosted Ubuntu runners.
set -euo pipefail
if (($# == 0)); then echo 'Usage: install_apt_packages.sh package...' >&2; exit 2; fi
packages=("$@")
missing=()
for package in "${packages[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
    missing+=("$package")
  fi
done
if ((${#missing[@]} == 0)); then echo "Packages already installed: ${packages[*]}"; exit 0; fi
apt_options=(-o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::ForceIPv4=true)
for attempt in 1 2 3; do
  echo "APT attempt $attempt/3: ${missing[*]}"
  if sudo apt-get "${apt_options[@]}" update && sudo apt-get "${apt_options[@]}" install -y --no-install-recommends --fix-missing "${missing[@]}"; then
    break
  fi
  if ((attempt == 3)); then echo '::error::APT failed after 3 attempts; check runner network/mirror.'; exit 1; fi
  sleep $((attempt * 10))
done
for package in "${missing[@]}"; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' || { echo "::error::Package verification failed: $package"; exit 1; }
done
