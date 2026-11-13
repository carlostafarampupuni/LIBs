#!/bin/bash
# Double-click to analyse every batch in a project and open the figures.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/libs ]; then
  echo "LIBs is not installed yet. Double-click 'Install LIBs (Mac).command' first."
  read -r -p "Press Enter to close."
  exit 1
fi

# Every folder here that holds notebooks/config.yaml is a project.
projects=()
for d in */; do
  [ -f "${d}notebooks/config.yaml" ] && projects+=("${d%/}")
done

if [ ${#projects[@]} -eq 0 ]; then
  echo "No project found next to this file."
  read -r -p "Press Enter to close."
  exit 1
elif [ ${#projects[@]} -eq 1 ]; then
  project="${projects[0]}"
else
  echo "Which project?"
  select project in "${projects[@]}"; do [ -n "$project" ] && break; done
fi

echo
echo "Analysing: $project"
echo
.venv/bin/libs run "$project" --open
echo
read -r -p "Press Enter to close."
