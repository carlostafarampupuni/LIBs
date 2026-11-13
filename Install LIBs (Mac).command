#!/bin/bash
# Double-click to install LIBs. Run it once per computer.
cd "$(dirname "$0")" || exit 1
echo "Installing LIBs in: $(pwd)"
echo

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Python is not installed."
  echo "Download it from https://www.python.org/downloads/ , install it, then double-click this file again."
  read -r -p "Press Enter to close."
  exit 1
fi

if "$PY" -m venv .venv \
   && .venv/bin/python -m pip install --upgrade pip \
   && .venv/bin/python -m pip install -e ".[all]"; then
  echo
  echo "LIBs is installed. Double-click 'Run LIBs (Mac).command' to analyse your data."
else
  echo
  echo "The installation stopped with an error (see above). Check your internet connection and try again."
fi
read -r -p "Press Enter to close."
