#!/usr/bin/env bash
# One-command setup for the secdogie game stack (Linux/macOS).
#
# The packages live in this repo and depend on each other but are NOT on PyPI,
# so `pip install secdogie-carjack` on its own fails (it can't find
# secdogie-aim). This installs them all into one venv, in the right order, in a
# single pip resolve so the local cross-deps satisfy each other.
#
#   ./install.sh              # game stack into ./.venv
#   ./install.sh --yolo       # + ultralytics (YOLO detector; large, needs a GPU to be fast)
#   ./install.sh --all        # + the non-game packages (scene3d, android, ios, open)
#   ./install.sh --venv PATH  # use/create a venv somewhere else
#
# Windows: use install.ps1 instead.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
WANT_YOLO=0
WANT_ALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --yolo) WANT_YOLO=1 ;;
    --all) WANT_ALL=1 ;;
    --venv) VENV="$2"; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

PY="${PYTHON:-python3}"
if [ ! -d "$VENV" ]; then
  echo "==> creating venv at $VENV"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip >/dev/null

# Dependency order (handoff has no local deps; aim needs handoff; carjack needs
# aim). All in one pip invocation so the resolver satisfies them locally.
GAME_PKGS=(./handoff ./agent ./aim ./carjack ./gta ./commander)
EXTRA_PKGS=(./scene3d ./android ./ios ./open)

PKGS=("${GAME_PKGS[@]}")
[ "$WANT_ALL" = 1 ] && PKGS+=("${EXTRA_PKGS[@]}")

echo "==> installing: ${PKGS[*]}"
EDITABLE=()
for p in "${PKGS[@]}"; do EDITABLE+=(-e "$p"); done
python -m pip install "${EDITABLE[@]}"

if [ "$WANT_YOLO" = 1 ]; then
  echo "==> installing ultralytics (YOLO). This is large; a GPU makes it real-time."
  python -m pip install ultralytics
  echo "    a stock yolov8n.pt (knows 'car', 'airplane', 'person', ...) auto-downloads on first use."
fi

cat <<EOF

Done. Activate the venv, then try (single-player games only):

  source $VENV/bin/activate
  secdogie-carjack --weights yolov8n.pt --label car --enter-key f   # walk to a car and get in
  secdogie-aim engage --weights dragon.pt --label ender_dragon --gain 0.4 --no-baton

Installed commands: secdogie-agent, secdogie-aim, secdogie-carjack, secdogie-gta, secdogie-commander$([ "$WANT_ALL" = 1 ] && echo ", secdogie-scene3d, secdogie-android, secdogie-ios, secdogie-open").
EOF
