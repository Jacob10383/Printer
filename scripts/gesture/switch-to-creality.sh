#!/bin/sh

LOCK_FILE="/tmp/ui-switch.lock"
STATE_FILE="/tmp/current-ui"


exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "ERROR: Another UI switch is in progress"
    exit 1
fi

echo "Switching to Creality UI..."

# Helper: Kill process with verification and retry
kill_verified() {
    proc=$1
    max_attempts=10
    attempt=0
    
    while pgrep "$proc" >/dev/null 2>&1; do
        killall -9 "$proc" 2>/dev/null || true
        sleep 0.1
        attempt=$((attempt + 1))
        if [ $attempt -ge $max_attempts ]; then
            echo "WARNING: Failed to kill $proc after $max_attempts attempts"
            break
        fi
    done
}

# Helper: Wait for process to start with verification
wait_for_process() {
    proc=$1
    max_attempts=20
    attempt=0
    
    echo "Waiting for $proc to start..."
    while ! pgrep "$proc" >/dev/null 2>&1; do
        sleep 0.5
        attempt=$((attempt + 1))
        if [ $attempt -ge $max_attempts ]; then
            echo "ERROR: $proc failed to start after $max_attempts attempts"
            exit 1
        fi
    done
    echo "$proc is running"
}


echo "Disabling GuppyScreen service..."
/etc/init.d/guppyscreen disable 2>/dev/null || true
/etc/init.d/guppyscreen stop 2>/dev/null || true


echo "Stopping GuppyScreen..."
kill_verified guppyscreen


echo "Restoring Creality UI binaries..."
[ -f /usr/bin/display-server.disabled ] && mv -f /usr/bin/display-server.disabled /usr/bin/display-server
[ -f /sbin/boot-play.disabled ] && mv -f /sbin/boot-play.disabled /sbin/boot-play

exec 200>&-


kill_verified display-server
kill_verified boot-play

echo "Waiting for watchdog to start Creality UI..."
wait_for_process display-server

/etc/init.d/gesture-daemon start 2>/dev/null || true

echo "creality" > "$STATE_FILE"

echo "Successfully switched to Creality UI"
echo "Tap top-right corner 5 times to switch back to GuppyScreen"
