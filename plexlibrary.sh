#!/bin/bash
#########################################################################
# Title:         Sandbox: Python-PlexLibrary Helper Script              #
# Author(s):     desimaniac                                             #
# URL:           https://github.com/saltyorg/Sandbox                    #
# --                                                                    #
# Modified for this repository:                                         #
#   - resolve the install path from the script's own location, so the   #
#     script is not tied to /opt/python-plexlibrary                     #
#   - capture stderr, where the app does all its logging; without this  #
#     the log file collects nothing but timestamps                      #
#   - preserve the app's exit code through the pipe                     #
#   - drop the cd, so relative paths (--config ./my.yml) still resolve  #
#     against the caller's directory                                    #
#   - rotate the log                                                    #
#########################################################################
#                   GNU General Public License v3.0                     #
#########################################################################

PATH='/usr/bin:/bin:/usr/local/bin'
export PYTHONIOENCODING=UTF-8

# Works when invoked directly and through a symlink such as
# /usr/local/bin/plexlibrary. Override with PLEXLIBRARY_PATH if needed.
APP_PATH="${PLEXLIBRARY_PATH:-$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")}"
PYTHON_PATH="$APP_PATH/venv/bin/python3"
LOG_PATH="$APP_PATH/plexlibrary.log"
MAX_LOG_BYTES=$((10 * 1024 * 1024))

if [ ! -x "$PYTHON_PATH" ]; then
    echo "No virtualenv found at $PYTHON_PATH" >&2
    echo "Create one with:" >&2
    echo "    python3 -m venv $APP_PATH/venv" >&2
    echo "    $APP_PATH/venv/bin/pip install -r $APP_PATH/requirements.txt" >&2
    exit 1
fi

if [ -f "$LOG_PATH" ] && [ "$(stat -c%s "$LOG_PATH" 2>/dev/null || echo 0)" -gt "$MAX_LOG_BYTES" ]; then
    mv "$LOG_PATH" "$LOG_PATH.1"
fi

{
    echo "$(date)"
    echo ""
} | tee -a "$LOG_PATH"

"$PYTHON_PATH" "$APP_PATH/plexlibrary" "$@" 2>&1 | tee -a "$LOG_PATH"
exit "${PIPESTATUS[0]}"
