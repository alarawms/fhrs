#!/bin/sh
set -e

if [ "$1" = "serve" ]; then
    alembic upgrade head
    # N9: environment is validated before traffic is served; a failed check stops
    # the container rather than serving silently degraded behavior.
    fhrs-doctor
    exec uvicorn fhrs.api.main:app --host 0.0.0.0 --port 8081
fi

exec "$@"
