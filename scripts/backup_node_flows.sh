#!/bin/bash

# Backup Node-RED flows from Docker container
# This script copies flows.json from nodecreator container and maintains only the last 10 backups

# Configuration
# Get the actual user's home directory (not root when using sudo)
if [ -n "$SUDO_USER" ]; then
    USER_HOME=$(eval echo ~"$SUDO_USER")
else
    USER_HOME="$HOME"
fi

BACKUP_DIR="$USER_HOME/Documents/flow_backups"
CONTAINER_NAME="nodecreator"
SOURCE_PATH="/root/.node-red/flows.json"
MAX_BACKUPS=10

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Generate timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/${TIMESTAMP}_node_flow.json"

# Copy flow from Docker container
echo "Backing up Node-RED flows from $CONTAINER_NAME..."
if sudo docker cp "$CONTAINER_NAME:$SOURCE_PATH" "$BACKUP_FILE"; then
    echo "Backup created: $BACKUP_FILE"

    # Count current backups
    BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/*_node_flow.json 2>/dev/null | wc -l)
    echo "Total backups: $BACKUP_COUNT"

    # Remove old backups if we exceed the limit
    if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
        REMOVE_COUNT=$((BACKUP_COUNT - MAX_BACKUPS))
        echo "Removing $REMOVE_COUNT old backup(s)..."
        ls -1t "$BACKUP_DIR"/*_node_flow.json | tail -n "$REMOVE_COUNT" | xargs rm -f
        echo "Cleanup completed. Kept $MAX_BACKUPS most recent backups."
    fi
else
    echo "Error: Failed to backup flows from $CONTAINER_NAME"
    exit 1
fi
