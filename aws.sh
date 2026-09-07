#!/usr/bin/env bash
# Compatibility entry point: installation logic lives in install.sh.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run this bootstrap as root.' >&2; exit 1; }
ref="${SPLASH_REF:-main}"
[[ "$ref" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]*$ ]] || { echo 'Invalid SPLASH_REF.' >&2; exit 1; }
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -s "$script_dir/install.sh" ]]; then exec bash "$script_dir/install.sh" "$@"; fi
command -v curl >/dev/null || { echo 'Install curl and ca-certificates first.' >&2; exit 1; }
work="$(mktemp -d /tmp/splash-init-bootstrap.XXXXXX)"
trap 'rm -rf -- "$work"' EXIT
curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
  --retry 5 --retry-all-errors --retry-delay 3 --retry-max-time 180 \
  --connect-timeout 15 --max-time 120 \
  --output "$work/install.sh" \
  "https://raw.githubusercontent.com/exirhub/splash-init/$ref/install.sh"
bash -n "$work/install.sh"
bash "$work/install.sh" "$@"
