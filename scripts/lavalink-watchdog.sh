#!/bin/sh
set -eu

: "${LAVALINK_URL:=http://lavalink:2333}"
: "${LAVALINK_PASSWORD:=adacord-internal-lavalink}"
: "${LAVALINK_HEALTH_IDENTIFIER:=ytmsearch%3Anever%20gonna%20give%20you%20up}"
: "${LAVALINK_WATCHDOG_INTERVAL:=60}"
: "${LAVALINK_WATCHDOG_TIMEOUT:=30}"
: "${LAVALINK_WATCHDOG_FAILURES:=3}"
: "${LAVALINK_WATCHDOG_START_PERIOD:=90}"
: "${LAVALINK_WATCHDOG_RESTART_CONTAINERS:=adacord-yt-cipher adacord-lavalink adacord-bot}"
: "${LAVALINK_WATCHDOG_STREAM_CANDIDATES:=3}"
: "${LAVALINK_WATCHDOG_STREAM_SAMPLE_BYTES:=1024}"

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

response_identifiers() {
  printf '%s' "$1" | grep -o '"identifier":"[^"]*"' | head -n "$LAVALINK_WATCHDOG_STREAM_CANDIDATES" | cut -d '"' -f 4
}

probe_youtube_stream() {
  video_id="$1"
  stream_url="${LAVALINK_URL%/}/youtube/stream/${video_id}"
  sample_file="/tmp/adacord-watchdog-stream-sample.$$"
  error_file="/tmp/adacord-watchdog-stream-error.$$"

  : > "$sample_file"
  : > "$error_file"
  wget \
    -q \
    -T "$LAVALINK_WATCHDOG_TIMEOUT" \
    -O - \
    --header="Authorization: $LAVALINK_PASSWORD" \
    "$stream_url" 2>"$error_file" \
    | head -c "$LAVALINK_WATCHDOG_STREAM_SAMPLE_BYTES" > "$sample_file"

  sample_size="$(wc -c < "$sample_file" | tr -d ' ')"
  if [ "$sample_size" -ge "$LAVALINK_WATCHDOG_STREAM_SAMPLE_BYTES" ]; then
    rm -f "$sample_file" "$error_file"
    return 0
  fi

  stream_probe_error="video=$video_id bytes=$sample_size error=$(response_excerpt "$(cat "$error_file")")"
  rm -f "$sample_file" "$error_file"
  return 1
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

  identifiers="$(response_identifiers "$response")"
  if [ -z "$identifiers" ]; then
    log "Lavalink playback probe returned no video identifiers response=$(response_excerpt "$response")"
    return 1
  fi

  stream_probe_error="no stream candidates were tested"
  for video_id in $identifiers; do
    if probe_youtube_stream "$video_id"; then
      return 0
    fi
  done

  log "Lavalink playback probe could not open media for any candidate: $stream_probe_error"
  return 1
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
