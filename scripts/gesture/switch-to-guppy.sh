#!/bin/sh


LOCK_FILE="/tmp/ui-switch.lock"
STATE_FILE="/tmp/current-ui"
LOG_FILE="/tmp/ui-switch-debug.log"

# Acquire exclusive lock using file descriptor
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "ERROR: Another UI switch is in progress"
    exit 1
fi

echo "=== $(date +%H:%M:%S) - Starting switch to GuppyScreen ===" | tee -a "$LOG_FILE"

# Helper: Kill process with verification
kill_verified() {
    proc=$1
    max_attempts=10
    attempt=0
    
    echo "  [KILL] Checking if $proc is running..." | tee -a "$LOG_FILE"
    pids=$(pgrep "$proc")
    if [ -z "$pids" ]; then
        echo "  [KILL] No $proc processes found" | tee -a "$LOG_FILE"
        return 0
    fi
    echo "  [KILL] Found $proc PIDs: $pids" | tee -a "$LOG_FILE"
    
    while pgrep "$proc" >/dev/null 2>&1; do
        pids=$(pgrep "$proc")
        echo "  [KILL] Attempt $((attempt + 1))/$max_attempts - Killing $proc (PIDs: $pids)" | tee -a "$LOG_FILE"
        killall -9 "$proc" 2>/dev/null || true
        sleep 0.1
        attempt=$((attempt + 1))
        if [ $attempt -ge $max_attempts ]; then
            echo "  [KILL] WARNING: Failed to kill $proc after $max_attempts attempts" | tee -a "$LOG_FILE"
            break
        fi
    done
    
    if ! pgrep "$proc" >/dev/null 2>&1; then
        echo "  [KILL] SUCCESS: $proc is dead" | tee -a "$LOG_FILE"
    else
        echo "  [KILL] FAILURE: $proc still running!" | tee -a "$LOG_FILE"
    fi
}

# Helper: Wait for process to start
wait_for_process() {
    proc=$1
    max_attempts=20
    attempt=0
    
    echo "  [WAIT] Waiting for $proc to start..." | tee -a "$LOG_FILE"
    while ! pgrep "$proc" >/dev/null 2>&1; do
        sleep 0.5
        attempt=$((attempt + 1))
        if [ $attempt -ge $max_attempts ]; then
            echo "  [WAIT] ERROR: $proc failed to start after $max_attempts attempts" | tee -a "$LOG_FILE"
            exit 1
        fi
    done
    pids=$(pgrep "$proc")
    echo "  [WAIT] SUCCESS: $proc is running (PIDs: $pids)" | tee -a "$LOG_FILE"
}

# Log current state
echo "[STATE] Before changes:" | tee -a "$LOG_FILE"
ps aux | grep -E 'display-server|guppyscreen|boot-play' | grep -v grep | tee -a "$LOG_FILE"

# Disable Creality binaries FIRST
echo "[DISABLE] Disabling Creality UI binaries..." | tee -a "$LOG_FILE"
[ -f /usr/bin/display-server ] && mv -f /usr/bin/display-server /usr/bin/display-server.disabled
[ -f /sbin/boot-play ] && mv -f /sbin/boot-play /sbin/boot-play.disabled

# Kill all UI processes
echo "[KILL] Starting kill sequence..." | tee -a "$LOG_FILE"
/etc/init.d/guppyscreen stop 2>&1 | tee -a "$LOG_FILE"
kill_verified display-server
kill_verified boot-play
kill_verified guppyscreen

# Start GuppyScreen
echo "[START] Starting GuppyScreen..." | tee -a "$LOG_FILE"


# Without this, GuppyScreen inherits FD 200 and holds the lock permanently
exec 200>&-

/etc/init.d/guppyscreen enable 2>&1 | tee -a "$LOG_FILE"
/etc/init.d/guppyscreen start 2>&1 | tee -a "$LOG_FILE"


wait_for_process guppyscreen


echo "guppy" > "$STATE_FILE"

echo "=== $(date +%H:%M:%S) - Successfully switched to GuppyScreen ===" | tee -a "$LOG_FILE"

