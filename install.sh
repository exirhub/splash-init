#!/usr/bin/env bash
# Source-safe; only main performs installation or changes the host.
set +x
set -Eeuo pipefail
# Keep a supplied token in this shell only. Pass it explicitly to our installer,
# never to curl's environment or the separately maintained public 3x-ui script.
export -n SPLASH_GITHUB_TOKEN

fail() { printf 'ERROR: %s\n' "$*" >&2; return 1; }
note() { printf '\n%s\n' "$*"; }
usage() {
  cat <<'EOF'
Usage: sudo bash install.sh [--update-only]
Fresh install uses x-ui.db only when no database existed.
Re-running preserves the existing database and installed 3x-ui version.
--update-only updates integration without host tuning or x-ui installation.
SPLASH_REF: commit/tag/branch (main); SPLASH_DB_TEMPLATE: x-ui.db (only).
The seed is validated before installation; its existing routing rules are kept.
PROXYFLEET_OUTBOUNDS_URL/TOKEN: initial values; existing settings are preserved.
SPLASH_GITHUB_TOKEN: GitHub token with Contents: read for this private repository.
Remote downloads prompt for the token when a terminal is available. No token is
saved; without a terminal, pass SPLASH_GITHUB_TOKEN for this invocation.
Exit 2: installation present, synchronization still pending.
EOF
}
preflight() {
  [[ ${EUID} -eq 0 ]] || { fail 'Run this installer as root.'; return 1; }
  [[ -r /etc/os-release ]] || { fail 'Debian or Ubuntu is required.'; return 1; }
  local ID='' ID_LIKE=''
  . /etc/os-release
  [[ "$ID" == debian || "$ID" == ubuntu ]] || { fail 'Only Debian and Ubuntu are supported.'; return 1; }
  command -v apt-get >/dev/null || { fail 'apt-get is required.'; return 1; }
  command -v systemctl >/dev/null && [[ -d /run/systemd/system ]] || { fail 'A running systemd host is required.'; return 1; }
  command -v flock >/dev/null || { fail 'Install util-linux (flock) before running this installer.'; return 1; }
  [[ ${SPLASH_REF:-main} =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]*$ ]] || { fail 'Invalid SPLASH_REF.'; return 1; }
  [[ ${SPLASH_DB_TEMPLATE:-x-ui.db} == x-ui.db ]] || { fail 'SPLASH_DB_TEMPLATE must be x-ui.db.'; return 1; }
}
install_prerequisites() {
  local missing=0 command
  for command in curl python3 flock; do command -v "$command" >/dev/null || missing=1; done
  [[ -s /etc/ssl/certs/ca-certificates.crt ]] || missing=1
  if (( missing )); then
    [[ ${UPDATE_ONLY:-0} -eq 0 ]] || { fail 'Install curl, python3, util-linux and ca-certificates before updating.'; return 1; }
    DEBIAN_FRONTEND=noninteractive apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl python3 util-linux
  fi
}
# Preserve the previous installer's DNS behavior on normal installs.
github_dns_ready() {
  getent ahostsv4 github.com >/dev/null 2>&1 &&
    getent ahostsv4 api.github.com >/dev/null 2>&1 &&
    getent ahostsv4 raw.githubusercontent.com >/dev/null 2>&1
}
configure_persistent_dns() {
  local interface resolv_tmp
  if command -v systemctl >/dev/null 2>&1 && command -v resolvectl >/dev/null 2>&1; then
    install -d -m 755 /etc/systemd/resolved.conf.d
    cat > /etc/systemd/resolved.conf.d/99-exir-dns.conf <<'EOF'
[Resolve]
DNS=1.1.1.1 8.8.8.8
FallbackDNS=9.9.9.9 8.8.4.4
EOF
    systemctl restart systemd-resolved 2>/dev/null || true
    resolvectl flush-caches 2>/dev/null || true
    interface="$(ip route show default 2>/dev/null | awk 'NR == 1 { print $5 }' || true)"
    if [[ -n "$interface" ]]; then
      resolvectl dns "$interface" 1.1.1.1 8.8.8.8 2>/dev/null || true
      resolvectl domain "$interface" '~.' 2>/dev/null || true
    fi
  fi
  if [[ -e /etc/resolv.conf && ! -e /etc/resolv.conf.exir-backup ]]; then
    cp -L /etc/resolv.conf /etc/resolv.conf.exir-backup 2>/dev/null || true
  fi
  resolv_tmp="$(mktemp)"
  printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\nnameserver 9.9.9.9\noptions timeout:2 attempts:3\n' > "$resolv_tmp"
  chmod 644 "$resolv_tmp"
  if ! cp --remove-destination "$resolv_tmp" /etc/resolv.conf; then
    rm -f -- "$resolv_tmp"
    fail 'Could not replace /etc/resolv.conf.'
    return 1
  fi
  rm -f -- "$resolv_tmp"
}
ensure_github_dns() {
  local attempt
  for attempt in 1 2 3 4 5 6; do
    github_dns_ready && return 0
    printf 'Waiting for GitHub DNS (%s/6)...\n' "$attempt" >&2
    sleep 5
  done
  if [[ ${UPDATE_ONLY:-0} -eq 1 ]]; then
    fail 'GitHub DNS is unavailable; update-only does not change DNS settings.'
    return 1
  fi
  configure_persistent_dns
  for attempt in 1 2 3 4 5 6; do github_dns_ready && return 0; sleep 2; done
  fail 'GitHub hosts could not be resolved. Check the server network/DNS configuration.'
}
download_file() {
  local url="$1" destination="$2"
  ensure_github_dns || return 1
  curl -q --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
    --retry 5 --retry-all-errors --retry-delay 3 --retry-max-time 180 \
    --connect-timeout 15 --max-time 120 --output "$destination" "$url" || return
  [[ -s "$destination" ]] || fail "Download was empty: $url"
}
require_splash_token() {
  set +x
  export -n SPLASH_GITHUB_TOKEN
  local tty_fd
  if [[ -z ${SPLASH_GITHUB_TOKEN:-} ]]; then
    if { exec {tty_fd}<>/dev/tty; } 2>/dev/null; then
      printf 'GitHub token for exirhub/splash-init (hidden): ' >&"$tty_fd"
      if ! IFS= read -r -s -u "$tty_fd" SPLASH_GITHUB_TOKEN; then
        printf '\n' >&"$tty_fd"
        exec {tty_fd}>&-
        fail 'A GitHub token is required for this private repository.'
        return 1
      fi
      printf '\n' >&"$tty_fd"
      exec {tty_fd}>&-
    else
      fail 'Private repository: set SPLASH_GITHUB_TOKEN for this invocation, or run from a terminal for a hidden token prompt.'
      return 1
    fi
  fi
  # RFC 6750 Bearer syntax also accepts GitHub Actions installation tokens.
  # Quotes, backslashes and whitespace cannot enter the curl config stream.
  [[ ${SPLASH_GITHUB_TOKEN:-} =~ ^[A-Za-z0-9._~+/-]+=*$ ]] || {
    fail 'Invalid GitHub token format; paste the token only, without spaces.'
    return 1
  }
}
github_api_file() {
  set +x
  local url="$1" destination="$2" accept="${3:-application/vnd.github.raw+json}" http_status
  # No redirects or custom curl configuration: credentials are restricted to
  # these two APIs in this repository, including when an endpoint redirects.
  [[ "$url" =~ ^https://api[.]github[.]com/repos/exirhub/splash-init/(contents/[A-Za-z0-9][A-Za-z0-9._/-]*[?]ref=[A-Za-z0-9][A-Za-z0-9._/-]*|commits/[A-Za-z0-9][A-Za-z0-9._/-]*)$ && "$url" != *..* ]] || {
    fail 'Refusing authenticated download outside the splash-init repository API.'
    return 1
  }
  case "$accept" in
    application/vnd.github.raw+json|application/vnd.github+json) ;;
    *) fail 'Invalid GitHub response format.'; return 1 ;;
  esac
  require_splash_token || return 1
  if ! http_status="$(
    printf 'header = "Authorization: Bearer %s"\n' "$SPLASH_GITHUB_TOKEN" |
      curl -q --config - --fail --silent --show-error --proto '=https' --tlsv1.2 \
        --retry 5 --retry-all-errors --retry-delay 3 --retry-max-time 180 \
        --connect-timeout 15 --max-time 120 --max-redirs 0 \
        --header "Accept: $accept" --header 'X-GitHub-Api-Version: 2022-11-28' \
        --write-out '%{http_code}' --output "$destination" "$url"
  )"; then
    rm -f -- "$destination"
    fail 'GitHub download failed. Check network access and token access to exirhub/splash-init (Contents: read; organization approval if required).'
    return 1
  fi
  [[ "$http_status" == 200 ]] || {
    rm -f -- "$destination"
    fail "GitHub returned HTTP $http_status; redirects and other unexpected responses are refused."
    return 1
  }
  [[ -s "$destination" ]] || {
    rm -f -- "$destination"
    fail 'GitHub returned an empty file.'
    return 1
  }
}
download_splash_file() {
  local path="$1" ref="$2" destination="$3"
  github_api_file "https://api.github.com/repos/exirhub/splash-init/contents/$path?ref=$ref" "$destination"
}
resolve_ref() {
  local ref="${SPLASH_REF:-main}" response
  if [[ "$ref" =~ ^[a-fA-F0-9]{40}$ ]]; then printf '%s\n' "${ref,,}"; return; fi
  response="$(mktemp "$WORK_DIR/commit.XXXXXX")"
  github_api_file "https://api.github.com/repos/exirhub/splash-init/commits/$ref" "$response" 'application/vnd.github+json' || return
  python3 - "$response" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle).get("sha", "")
if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
    raise SystemExit("GitHub returned an invalid splash-init commit SHA")
print(value)
PY
}
bundle_files() {
  printf '%s\n' install.sh helpers/manage.py \
    vendor/proxyfleet-xui-sync/install.sh \
    vendor/proxyfleet-xui-sync/proxyfleet-xui-sync.py \
    vendor/proxyfleet-xui-sync/proxyfleet-xui-sync.env.example \
    vendor/proxyfleet-xui-sync/systemd/proxyfleet-xui-sync.service \
    vendor/proxyfleet-xui-sync/systemd/proxyfleet-xui-sync.timer
}
prepare_bundle() {
  local file complete=1 source_dir
  source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  while IFS= read -r file; do [[ -s "$source_dir/$file" ]] || complete=0; done < <(bundle_files)
  if (( complete )); then
    BUNDLE_DIR="$source_dir"
    BUNDLE_REF=local
    note "Using complete local splash-init checkout: $BUNDLE_DIR"
  else
    require_splash_token
    BUNDLE_REF="$(resolve_ref)"
    BUNDLE_DIR="$WORK_DIR/bundle"
    install -d -m 700 "$BUNDLE_DIR"
    note "Downloading splash-init bundle at commit $BUNDLE_REF"
    while IFS= read -r file; do
      install -d -m 700 "$(dirname -- "$BUNDLE_DIR/$file")"
      download_splash_file "$file" "$BUNDLE_REF" "$BUNDLE_DIR/$file"
    done < <(bundle_files)
  fi
  python3 -m py_compile "$BUNDLE_DIR/helpers/manage.py" "$BUNDLE_DIR/vendor/proxyfleet-xui-sync/proxyfleet-xui-sync.py"
  bash -n "$BUNDLE_DIR/install.sh"
  bash -n "$BUNDLE_DIR/vendor/proxyfleet-xui-sync/install.sh"
}
prepare_seed() {
  local template="${SPLASH_DB_TEMPLATE:-x-ui.db}"
  SEED_PATH="$BUNDLE_DIR/$template"
  if [[ ! -s "$SEED_PATH" ]]; then
    [[ "$BUNDLE_REF" != local ]] || { fail "Local checkout is missing $template."; return 1; }
    download_splash_file "$template" "$BUNDLE_REF" "$SEED_PATH"
  fi
  python3 "$BUNDLE_DIR/helpers/manage.py" validate-seed "$SEED_PATH"
}
wait_for_xui() {
  local attempt
  for attempt in {1..15}; do
    if systemctl is-active --quiet x-ui; then
      sleep 2
      systemctl is-active --quiet x-ui && return 0
    fi
    sleep 1
  done
  return 1
}
seed_database() {
  local template="$1" backup_dir backup_path replacement db_dir
  db_dir="$(dirname -- "$XUI_DB_PATH")"
  python3 "$BUNDLE_DIR/helpers/manage.py" validate-seed "$template" || return
  install -d -o root -g root -m 700 "$db_dir" "$XUI_BACKUP_DIR"
  # Prepare the replacement before stopping a service. Recovery survives EXIT.
  replacement="$(mktemp "$db_dir/.splash-seed.XXXXXX")"
  if ! install -o root -g root -m 600 "$template" "$replacement"; then
    rm -f -- "$replacement"
    fail 'Could not prepare the seed; x-ui was not stopped.'
    return 1
  fi
  backup_dir="$(mktemp -d "$XUI_BACKUP_DIR/pre-seed.XXXXXX")"
  backup_path="$backup_dir/x-ui.db"
  if ! systemctl stop x-ui; then
    rm -f -- "$replacement"
    fail 'Could not stop x-ui; database was not changed.'
    return 1
  fi
  if systemctl is-active --quiet x-ui || pgrep -x x-ui >/dev/null; then
    rm -f -- "$replacement"
    fail 'x-ui has not stopped; database was not changed.'
    return 1
  fi
  if [[ -e "$XUI_DB_PATH" ]]; then
    if ! python3 "$BUNDLE_DIR/helpers/manage.py" backup-db "$XUI_DB_PATH" "$backup_path"; then
      rm -f -- "$replacement"
      systemctl start x-ui || true
      fail 'Database backup failed; seed was not applied.'
      return 1
    fi
  fi
  rm -f -- "$XUI_DB_PATH-wal" "$XUI_DB_PATH-shm"
  if ! mv -f -- "$replacement" "$XUI_DB_PATH"; then
    systemctl start x-ui || true
    fail "Seed replacement failed; consistent backup retained at $backup_path"
    return 1
  fi
  if systemctl start x-ui && wait_for_xui; then
    note 'Fresh splash database installed; x-ui is running.'
    return 0
  fi
  printf 'ERROR: x-ui failed after seeding; restoring the pre-seed database.\n' >&2
  systemctl stop x-ui || true
  if systemctl is-active --quiet x-ui || pgrep -x x-ui >/dev/null; then
    fail "Cannot safely restore while x-ui is running. Backup: $backup_path"
    return 1
  fi
  rm -f -- "$XUI_DB_PATH-wal" "$XUI_DB_PATH-shm"
  if [[ -f "$backup_path" ]]; then
    replacement="$(mktemp "$db_dir/.splash-restore.XXXXXX")"
    if ! install -o root -g root -m 600 "$backup_path" "$replacement" || ! mv -f -- "$replacement" "$XUI_DB_PATH"; then
      fail "Restore failed; recover the retained backup manually: $backup_path"
      return 1
    fi
    if ! systemctl start x-ui || ! wait_for_xui; then
      fail "Pre-seed database restored but x-ui did not start. Backup: $backup_path"
      return 1
    fi
  else
    rm -f -- "$XUI_DB_PATH"
  fi
  fail 'Splash seed failed; the pre-seed database was restored. See journalctl -u x-ui.'
}
ensure_xui() {
  local fresh=0 installer
  [[ -e "$XUI_DB_PATH" ]] || fresh=1
  if (( fresh )); then prepare_seed; fi
  if [[ ! -x "$XUI_BINARY" ]]; then
    installer="$WORK_DIR/3x-ui-install.sh"
    download_file 'https://raw.githubusercontent.com/MHSanaei/3x-ui/main/install.sh' "$installer"
    bash -n "$installer"
    if ! XUI_NONINTERACTIVE=1 bash "$installer" </dev/null; then
      fail 'Official 3x-ui installation failed; no splash database replacement was performed.'
      return 1
    fi
  else
    note 'Existing 3x-ui installation preserved; no upgrade requested.'
  fi
  if (( fresh )); then
    seed_database "$SEED_PATH"
  else
    note 'Existing x-ui database preserved.'
    systemctl start x-ui
    wait_for_xui || fail 'Existing x-ui did not start; database was not replaced.'
  fi
}
detect_xray() {
  local candidate arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) candidate=/usr/local/x-ui/bin/xray-linux-amd64 ;;
    aarch64|arm64) candidate=/usr/local/x-ui/bin/xray-linux-arm64 ;;
    *) fail "Unsupported Xray architecture: $arch"; return 1 ;;
  esac
  [[ -x "$candidate" ]] || { fail "Installed Xray binary is missing: $candidate"; return 1; }
  printf '%s\n' "$candidate"
}
configure_sync_env() {
  local binary
  binary="$(detect_xray)"
  python3 "$BUNDLE_DIR/helpers/manage.py" configure-env \
    --env-file "$SYNC_ENV_FILE" \
    --example "$BUNDLE_DIR/vendor/proxyfleet-xui-sync/proxyfleet-xui-sync.env.example" \
    --xray-binary "$binary"
}
install_sync() {
  if ! bash "$BUNDLE_DIR/vendor/proxyfleet-xui-sync/install.sh"; then
    fail 'ProxyFleet sync did not complete. Inspect journalctl -u proxyfleet-xui-sync.service, then run splash-init-update.'
    return 1
  fi
  systemctl is-enabled --quiet proxyfleet-xui-sync.timer || { fail 'ProxyFleet timer is not enabled.'; return 1; }
  systemctl is-active --quiet proxyfleet-xui-sync.timer || fail 'ProxyFleet timer is not running.'
}
render_updater() {
  cat <<'UPDATER'
#!/usr/bin/env bash
set +x
set -Eeuo pipefail
export -n SPLASH_GITHUB_TOKEN
UPDATER
  # Function declarations contain no token values, so the updater keeps no
  # credentials. A hidden prompt works on a fresh shell without any setup.
  declare -f fail require_splash_token github_api_file download_splash_file
  cat <<'UPDATER'
[[ $EUID -eq 0 ]] || { echo 'Run splash-init-update as root.' >&2; exit 1; }
ref="${SPLASH_REF:-main}"
[[ "$ref" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]*$ ]] || { echo 'Invalid SPLASH_REF.' >&2; exit 1; }
command -v curl >/dev/null || { echo 'Install curl and ca-certificates first.' >&2; exit 1; }
work="$(mktemp -d /tmp/splash-init-update.XXXXXX)"
trap 'rm -rf -- "$work"' EXIT
require_splash_token
download_splash_file install.sh "$ref" "$work/install.sh"
bash -n "$work/install.sh"
SPLASH_GITHUB_TOKEN="$SPLASH_GITHUB_TOKEN" bash "$work/install.sh" --update-only "$@"
UPDATER
}
install_updater() {
  install -d -m 755 /usr/local/lib/splash-init /usr/local/sbin
  install -o root -g root -m 750 "$BUNDLE_DIR/install.sh" /usr/local/lib/splash-init/install.sh
  printf '%s\n' "$BUNDLE_REF" > /usr/local/lib/splash-init/installed-ref
  render_updater > /usr/local/sbin/splash-init-update
  chmod 750 /usr/local/sbin/splash-init-update
  chown root:root /usr/local/sbin/splash-init-update
}
apply_host_defaults() {
  command -v ufw >/dev/null && ufw disable || true
  if ! swapon --show=NAME --noheadings 2>/dev/null | grep -qx '/swapfile'; then
    if [[ ! -e /swapfile ]]; then
      fallocate -l 1G /swapfile
      chmod 600 /swapfile
      mkswap /swapfile
    else
      chmod 600 /swapfile
      [[ "$(blkid -p -s TYPE -o value /swapfile 2>/dev/null || true)" == swap ]] || { fail 'Existing /swapfile is not swap; refusing to format it.'; return 1; }
    fi
    swapon /swapfile
  fi
  grep -qE '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab || printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
  cat > /etc/sysctl.d/99-exir-network.conf <<'EOF'
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.core.netdev_max_backlog = 100000
net.ipv4.tcp_keepalive_time = 60
net.ipv4.tcp_keepalive_intvl = 10
net.ipv4.tcp_keepalive_probes = 6
EOF
  sysctl -p /etc/sysctl.d/99-exir-network.conf
}
cleanup() {
  if [[ ${KEEP_WORK_DIR:-0} -eq 1 ]]; then
    printf 'Recovery files retained at %s\n' "$WORK_DIR" >&2
  elif [[ -n ${WORK_DIR:-} && -d "$WORK_DIR" ]]; then
    rm -rf -- "$WORK_DIR"
  fi
}
initialize_paths() {
  WORK_DIR="$(mktemp -d /tmp/splash-init.XXXXXX)"
  KEEP_WORK_DIR=0
  XUI_DB_PATH=/etc/x-ui/x-ui.db
  XUI_BINARY=/usr/local/x-ui/x-ui
  XUI_BACKUP_DIR=/var/lib/splash-init/backups
  SYNC_ENV_FILE=/etc/proxyfleet-xui-sync.env
}
acquire_lock() {
  exec 9>/run/lock/splash-init.lock
  flock -n 9 || fail 'Another splash-init installation is running.'
}
check_existing_sync_config() {
  local line key value
  [[ -e "$SYNC_ENV_FILE" ]] || return 0
  # These two stock settings cannot be relocated by this bootstrap. Parse only
  # literal values; never execute/source a systemd EnvironmentFile as shell code.
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(XUI_DB|XUI_SERVICE)= ]] || continue
    key="${BASH_REMATCH[1]}"
    value="${line#*=}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    if [[ "$key" == XUI_DB && "$value" != /etc/x-ui/x-ui.db ]] || [[ "$key" == XUI_SERVICE && "$value" != x-ui ]]; then
      fail "Existing $key uses a custom value; splash-init supports /etc/x-ui/x-ui.db and x-ui.service only. No host changes were applied."
      return 1
    fi
  done < "$SYNC_ENV_FILE"
}
verify_existing_xui() {
  [[ -s "$XUI_DB_PATH" && -x "$XUI_BINARY" ]] || { fail 'Update requires an existing 3x-ui installation and database.'; return 1; }
  systemctl is-active --quiet x-ui || fail 'Start x-ui before updating its integration.'
}
run_installation() {
  local status
  if (( UPDATE_ONLY )); then
    verify_existing_xui
  else
    ensure_xui
    apply_host_defaults
  fi
  configure_sync_env
  install_updater
  install_sync
  note 'ProxyFleet sync and its retry timer are installed. Checking actual readiness...'
  if python3 "$BUNDLE_DIR/helpers/manage.py" status --env-file "$SYNC_ENV_FILE"; then
    note 'Splash integration installed; local configuration is ready. Future updates: sudo splash-init-update'
  else
    status=$?
    if (( status == 2 )); then
      note 'INSTALLED, SYNC PENDING: the timer will retry. Fleet readiness or topology stability is pending. This is not a completed sync.'
    else
      printf 'ERROR: Integration installed, but readiness validation failed. Check the sync service logs.\n' >&2
    fi
    return "$status"
  fi
}
main() {
  UPDATE_ONLY=0
  while (( $# )); do
    case "$1" in
      --update-only) UPDATE_ONLY=1 ;;
      -h|--help) usage; return 0 ;;
      *) fail "Unknown argument: $1"; return 1 ;;
    esac
    shift
  done
  preflight
  acquire_lock
  initialize_paths
  trap cleanup EXIT
  check_existing_sync_config
  if (( ! UPDATE_ONLY )); then configure_persistent_dns; fi
  install_prerequisites
  prepare_bundle
  if [[ "$BUNDLE_REF" != local ]]; then
    # The initial downloaded script is only a bootstrap. Load installation
    # functions from the same immutable commit as its helpers and sync assets.
    . "$BUNDLE_DIR/install.sh"
  fi
  run_installation
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
