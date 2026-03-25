#!/bin/bash
# Reproduces the blkid storm seen before kernel panics on production systems.
# This simulates what happens when inspections call list_usb_paths() rapidly.
# Run as root. Watch for system lockup/panic.
# Usage: sudo bash reproduce_blkid_storm.sh [calls_per_second] [duration_seconds]

RATE=${1:-20}
DURATION=${2:-120}
END=$((SECONDS + DURATION))

echo "Reproducing blkid storm: ~${RATE} calls/sec for ${DURATION}s"
echo "Monitor with: journalctl -f -k"
echo "Press Ctrl+C to stop"
echo ""

SLEEP_TIME=$(echo "scale=4; 1/$RATE" | bc)
COUNT=0

while [ $SECONDS -lt $END ]; do
    sudo blkid -t TYPE=vfat -o device &
    sudo blkid -t TYPE=exfat -o device &
    COUNT=$((COUNT + 2))
    sleep $SLEEP_TIME
done

wait
echo "Done. Spawned $COUNT blkid calls."
