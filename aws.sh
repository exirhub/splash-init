#!/usr/bin/env bash
# Compatibility bootstrap for the private splash-init repository.
set +x
set -Eeuo pipefail
export -n SPLASH_GITHUB_TOKEN
fail () 
{ 
    printf 'ERROR: %s\n' "$*" 1>&2;
    return 1
}
require_splash_token () 
{ 
    set +x;
    export -n SPLASH_GITHUB_TOKEN;
    local tty_fd;
    if [[ -z ${SPLASH_GITHUB_TOKEN:-} ]]; then
        if { 
            exec {tty_fd}<> /dev/tty
        } 2> /dev/null; then
            printf 'GitHub token for exirhub/splash-init (hidden): ' >&"$tty_fd";
            if ! IFS= read -r -s -u "$tty_fd" SPLASH_GITHUB_TOKEN; then
                printf '\n' >&"$tty_fd";
                exec {tty_fd}>&-;
                fail 'A GitHub token is required for this private repository.';
                return 1;
            fi;
            printf '\n' >&"$tty_fd";
            exec {tty_fd}>&-;
        else
            fail 'Private repository: set SPLASH_GITHUB_TOKEN for this invocation, or run from a terminal for a hidden token prompt.';
            return 1;
        fi;
    fi;
    [[ ${SPLASH_GITHUB_TOKEN:-} =~ ^[A-Za-z0-9_]+$ ]] || { 
        fail 'Invalid GitHub token format; paste the token only, without spaces.';
        return 1
    }
}
github_api_file () 
{ 
    set +x;
    local url="$1" destination="$2" accept="${3:-application/vnd.github.raw+json}" http_status;
    [[ "$url" =~ ^https://api[.]github[.]com/repos/exirhub/splash-init/(contents/[A-Za-z0-9][A-Za-z0-9._/-]*[?]ref=[A-Za-z0-9][A-Za-z0-9._/-]*|commits/[A-Za-z0-9][A-Za-z0-9._/-]*)$ && "$url" != *..* ]] || { 
        fail 'Refusing authenticated download outside the splash-init repository API.';
        return 1
    };
    case "$accept" in 
        application/vnd.github.raw+json | application/vnd.github+json)

        ;;
        *)
            fail 'Invalid GitHub response format.';
            return 1
        ;;
    esac;
    require_splash_token || return 1;
    if ! http_status="$(printf 'header = "Authorization: Bearer %s"\n' "$SPLASH_GITHUB_TOKEN" | curl -q --config - --fail --silent --show-error --proto '=https' --tlsv1.2 --retry 5 --retry-all-errors --retry-delay 3 --retry-max-time 180 --connect-timeout 15 --max-time 120 --max-redirs 0 --header "Accept: $accept" --header 'X-GitHub-Api-Version: 2022-11-28' --write-out '%{http_code}' --output "$destination" "$url")"; then
        rm -f -- "$destination";
        fail 'GitHub download failed. Check network access and token access to exirhub/splash-init (Contents: read; organization approval if required).';
        return 1;
    fi;
    [[ "$http_status" == 200 ]] || { 
        rm -f -- "$destination";
        fail "GitHub returned HTTP $http_status; redirects and other unexpected responses are refused.";
        return 1
    };
    [[ -s "$destination" ]] || { 
        rm -f -- "$destination";
        fail 'GitHub returned an empty file.';
        return 1
    }
}
download_splash_file () 
{ 
    local path="$1" ref="$2" destination="$3";
    github_api_file "https://api.github.com/repos/exirhub/splash-init/contents/$path?ref=$ref" "$destination"
}
main() {
  [[ $EUID -eq 0 ]] || { echo 'Run this bootstrap as root.' >&2; return 1; }
  local ref="${SPLASH_REF:-main}" script_dir work
  [[ "$ref" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]*$ ]] || { echo 'Invalid SPLASH_REF.' >&2; return 1; }
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -s "$script_dir/install.sh" ]]; then
    SPLASH_GITHUB_TOKEN="${SPLASH_GITHUB_TOKEN:-}" exec bash "$script_dir/install.sh" "$@"
  fi
  command -v curl >/dev/null || { echo 'Install curl and ca-certificates first.' >&2; return 1; }
  work="$(mktemp -d /tmp/splash-init-bootstrap.XXXXXX)"
  # Keep this variable available to the EXIT trap after main returns.
  SPLASH_BOOTSTRAP_WORK="$work"
  trap 'rm -rf -- "$SPLASH_BOOTSTRAP_WORK"' EXIT
  require_splash_token
  download_splash_file install.sh "$ref" "$work/install.sh"
  bash -n "$work/install.sh"
  SPLASH_GITHUB_TOKEN="$SPLASH_GITHUB_TOKEN" bash "$work/install.sh" "$@"
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
