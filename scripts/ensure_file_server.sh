#!/bin/sh
# ensure_file_server.sh <port> <start script>
# Leave one healthy file server on <port>: do nothing if it answers, otherwise
# clear every copy and start one under forever. Safe to run repeatedly; cron
# runs it as a watchdog.
#
# A server orphaned from its forever monitor kept listening but dropped every
# request (its stdout pipe was gone), so Time Machine clips played as black.
# Listening is not enough - it has to answer, and answer with byte ranges:
# python's http.server could not seek a clip, so an old one is replaced too.
PORT="$1"
SCRIPT="$2"

if curl -sSI -m 5 "http://127.0.0.1:$PORT/" 2>/dev/null | grep -qi '^accept-ranges: *bytes'; then
    exit 0
fi

# forever stop only takes one match per call, and each run of the old
# scripts added another monitor
i=0
while [ "$i" -lt 20 ] && forever list 2>/dev/null | grep -qF "$SCRIPT"; do
    forever stop "$SCRIPT" >/dev/null 2>&1
    i=$((i + 1))
done
pkill -f "http.server $PORT " 2>/dev/null
pkill -f "range_http_server.py $PORT " 2>/dev/null
sleep 1

forever start -c sh "$SCRIPT"
