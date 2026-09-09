#!/bin/zsh

set -u

PROJECT_DIR="${0:A:h:h}"
PYTHON="$PROJECT_DIR/.venv/bin/python"
RUNNER="$PROJECT_DIR/scripts/stream_pokemon.py"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/macos_launcher.log"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/Library/Android/sdk/platform-tools:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1

if [[ ! -f "$RUNNER" || ! -x "$PYTHON" ]]; then
    /usr/bin/osascript -e 'display alert "Pokémon GO Storage Manager" message "Create .venv in the project folder and install requirements.txt before starting the launcher." as critical'
    exit 1
fi

existing_pid="$(/usr/bin/pgrep -f '([r]un.py gui|[s]cripts/stream_pokemon.py)' | /usr/bin/head -n 1 || true)"
if [[ -n "$existing_pid" ]]; then
    /usr/bin/osascript -e 'display notification "The manager is already open; no second copy was started." with title "Pokémon GO Storage Manager"' >/dev/null 2>&1 || true
    exit 0
fi

/bin/mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR" || exit 1

# Keep the Mac and display awake for as long as the GUI process is alive.
/usr/bin/caffeinate -dimsu -w "$$" >/dev/null 2>&1 &

exec "$PYTHON" "$RUNNER" >>"$LOG_FILE" 2>&1
