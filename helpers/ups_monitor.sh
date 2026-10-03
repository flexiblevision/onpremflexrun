#!/bin/bash
# UPS monitor - logs apcaccess status as CSV every second to /var/log/ups.log

LOG_FILE="/var/log/ups.log"
HEADER="timestamp,status,line_v,output_v,load_pct,bcharge_pct,timeleft_min,numxfers,tonbatt_s,cumonbatt_s"

# Write header if file doesn't exist or is empty
[ ! -s "$LOG_FILE" ] && echo "$HEADER" >> "$LOG_FILE"

while true; do
    RAW=$(apcaccess status 2>/dev/null)
    get() { echo "$RAW" | grep -i "^$1" | cut -d: -f2 | awk '{print $1}'; }

    echo "$(date '+%Y-%m-%d %H:%M:%S'),$(get STATUS),$(get LINEV),$(get OUTPUTV),$(get LOADPCT),$(get BCHARGE),$(get TIMELEFT),$(get NUMXFERS),$(get TONBATT),$(get CUMONBATT)" >> "$LOG_FILE"
    sleep 1
done
