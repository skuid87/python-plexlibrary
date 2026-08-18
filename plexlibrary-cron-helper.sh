#!/bin/bash
#########################################################################
# Title:         Sandbox: Python-PlexLibrary Cron Helper Script         #
# Author(s):     desimaniac                                             #
# URL:           https://github.com/saltyorg/Sandbox                    #
# --                                                                    #
# Modified for this repository:                                         #
#   - resolve the install path from the script's own location, so the   #
#     script is not tied to /opt/python-plexlibrary                     #
#   - hold a lock, so two runs cannot rewrite the shared guid cache at  #
#     the same time                                                     #
#   - capture stderr, where the app does all its logging                #
#   - report each recipe's real exit code instead of tee's, and exit    #
#     non-zero if any recipe failed, so monitoring can see it           #
#   - only iterate *.yml, and keep going after a failed recipe          #
#   - rotate the log                                                    #
#########################################################################
#                   GNU General Public License v3.0                     #
#########################################################################

PATH='/usr/bin:/bin:/usr/local/bin'
export PYTHONIOENCODING=UTF-8

APP_PATH="${PLEXLIBRARY_PATH:-$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")}"
PYTHON_PATH="$APP_PATH/venv/bin/python3"
LOG_PATH="$APP_PATH/plexlibrary-cron.log"
RECIPES_PATH="$APP_PATH/recipes"
LOCK_PATH='/tmp/plexlibrary-cron.lock'
MAX_LOG_BYTES=$((10 * 1024 * 1024))

# Every recipe shares one guid cache file, which is rewritten wholesale on
# save, so overlapping runs can clobber it. Skip rather than queue: the next
# scheduled run will pick things up.
exec 9>"$LOCK_PATH"
if ! flock -n 9; then
    echo "$(date): a previous run is still going, skipping" | tee -a "$LOG_PATH"
    exit 0
fi

if [ ! -x "$PYTHON_PATH" ]; then
    echo "No virtualenv found at $PYTHON_PATH" >&2
    exit 1
fi

if [ -f "$LOG_PATH" ] && [ "$(stat -c%s "$LOG_PATH" 2>/dev/null || echo 0)" -gt "$MAX_LOG_BYTES" ]; then
    mv "$LOG_PATH" "$LOG_PATH.1"
fi

echo "$(date)" | tee -a "$LOG_PATH"
echo "" | tee -a "$LOG_PATH"

failed=0

for file in "$RECIPES_PATH"/*.yml; do
    [ -f "$file" ] || continue
    recipe="$(basename "$file" .yml)"

    "$PYTHON_PATH" "$APP_PATH/plexlibrary" "$recipe" 2>&1 | tee -a "$LOG_PATH"
    status=${PIPESTATUS[0]}

    if [ "$status" -ne 0 ]; then
        echo "!! recipe '$recipe' failed with exit code $status" | tee -a "$LOG_PATH"
        failed=1
    fi

    echo "" | tee -a "$LOG_PATH"
done

exit "$failed"
