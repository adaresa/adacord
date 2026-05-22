#!/bin/sh
set -eu

: "${LAVALINK_URL:=http://lavalink:2333}"
: "${LAVALINK_PASSWORD:=adacord-internal-lavalink}"
: "${LAVALINK_HEALTH_IDENTIFIER:=ytmsearch%3Anever%20gonna%20give%20you%20up}"
: "${LAVALINK_WATCHDOG_INTERVAL:=300}"
: "${LAVALINK_WATCHDOG_TIMEOUT:=30}"
: "${LAVALINK_WATCHDOG_FAILURES:=3}"
: "${LAVALINK_WATCHDOG_START_PERIOD:=90}"
: "${LAVALINK_WATCHDOG_RESTART_CONTAINERS:=adacord-yt-cipher adacord-lavalink adacord-bot}"

failures=0

log() {
  printf '%s %s\n' "$(date -Iseconds)" "$*"
}

response_excerpt() {
  printf '%s' "$1" | tr '\r\n' '  ' | cut -c 1-500
}

response_load_type() {
  printf '%s' "$1" | sed -n 's/.*"loadType":"\([^"]*\)".*/\1/p' | head -n 1
}

probe_lavalink() {
  url="${LAVALINK_URL%/}/v4/loadtracks?identifier=${LAVALINK_HEALTH_IDENTIFIER}"
  response="$(
    wget \
      -q \
      -T "$LAVALINK_WATCHDOG_TIMEOUT" \
      -O - \
      --header="Authorization: $LAVALINK_PASSWORD" \
      "$url" 2>&1
  )" || {
    log "Lavalink playback probe failed: $(response_excerpt "$response")"
    return 1
  }

  if ! printf '%s' "$response" | grep -q '"loadType":"search"'; then
    load_type="$(response_load_type "$response")"
    if [ -z "$load_type" ]; then
      load_type="<missing>"
    fi
    log "Lavalink playback probe returned unexpected loadType=$load_type response=$(response_excerpt "$response")"
    return 1
  fi

  if ! printf '%s' "$response" | grep -q '"encoded"'; then
    log "Lavalink playback probe returned no tracks response=$(response_excerpt "$response")"
    return 1
  fi
}

restart_music_stack() {
  log "Restarting music stack after $failures failed playback probes"
  for container in $LAVALINK_WATCHDOG_RESTART_CONTAINERS; do
    docker restart "$container" >/dev/null || log "Could not restart $container"
  done
  failures=0
  sleep "$LAVALINK_WATCHDOG_START_PERIOD"
}

log "Starting Lavalink playback watchdog"
sleep "$LAVALINK_WATCHDOG_START_PERIOD"

while :; do
  if probe_lavalink; then
    if [ "$failures" -gt 0 ]; then
      log "Lavalink playback probe recovered"
    fi
    failures=0
  else
    failures=$((failures + 1))
    log "Lavalink playback probe failure $failures/$LAVALINK_WATCHDOG_FAILURES"
    if [ "$failures" -ge "$LAVALINK_WATCHDOG_FAILURES" ]; then
      restart_music_stack
    fi
  fi

  sleep "$LAVALINK_WATCHDOG_INTERVAL"
done
