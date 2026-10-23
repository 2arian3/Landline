# Postgres 17 with pgvector and PostGIS. Used locally (docker-compose.yml) and in production (deploy/compose.yml).
# Based on the pgvector image (Debian bookworm, PGDG repo already configured);
# the postgis/postgis images are still on bullseye, whose PGDG repo is archived.
FROM pgvector/pgvector:pg17
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-postgis-3 \
 && rm -rf /var/lib/apt/lists/*
