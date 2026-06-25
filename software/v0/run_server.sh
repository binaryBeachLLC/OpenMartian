#!/bin/bash
# Launch the OpenMartian dev console (UI + API) on http://localhost:8000
#   UI:        http://localhost:8000/
#   API docs:  http://localhost:8000/docs   (the agent-drivable interface)
cd "$(dirname "$0")" || exit 1
exec uvicorn webapp.app:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
