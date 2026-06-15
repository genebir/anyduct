# anyduct-postgres — metadata DB image with the alembic schema pre-applied.
#
# Why this exists
# ---------------
# A fresh `postgres:16-alpine` boots empty. Bootstrapping the anyduct
# metadata DB on first run means waiting for `anyduct-migrate` to apply
# every Alembic revision. That's fine in long-lived deployments but
# painful for demos, ephemeral environments, and CI. This image ships
# with the schema **already loaded** (DDL + `alembic_version` marker)
# so the database is ready the moment Postgres reports healthy.
#
# How it works
# ------------
# We use the upstream `postgres` image's standard
# `/docker-entrypoint-initdb.d/` convention: any `.sql` / `.sh` in that
# directory runs once on first boot, before the database accepts
# external connections. The committed `init/` files (`00-schema.sql`,
# `01-alembic-head.sql`) replace what `alembic upgrade head` would do.
# `anyduct-migrate` is still safe to run against this image — `alembic
# upgrade head` is a no-op when `alembic_version` already matches.
#
# Building
# --------
#     docker build -f services/anyduct-postgres/Dockerfile \
#                  -t anyduct-postgres:<tag> services/anyduct-postgres/
#
# The `init/` files are committed to the repo. Regenerate them after
# a new Alembic revision lands:
#
#     make seed-schema           # → services/anyduct-postgres/init/00-*.sql
#
# Demo data
# ---------
# Build with `--build-arg INCLUDE_DEMO_SEED=1` to also ship a sample
# workspace + a few records. The base image has NO seed data so it's
# safe to use as the metadata DB for fresh production deployments.

FROM postgres:16-alpine

# Default credentials for the demo workspace + base scripts. Override
# at run time via the standard postgres env vars. These values match
# `services/docker-compose.prod.yml` so an out-of-the-box run works.
ENV POSTGRES_USER=anyduct \
    POSTGRES_PASSWORD=anyduct \
    POSTGRES_DB=anyduct

# Bake the schema + alembic head marker into the image. These files
# come from `regen-schema.sh` / `make seed-schema` and are committed
# to the repo so the build is reproducible without needing Python or
# Alembic at image build time.
COPY init/ /docker-entrypoint-initdb.d/

# Optional demo data lives in a separate dir; an entrypoint shim
# decides whether to also copy that in based on $INCLUDE_DEMO_SEED.
# Default OFF so a plain `docker run` produces a production-safe
# (empty) DB.
ARG INCLUDE_DEMO_SEED=0
COPY demo/ /opt/anyduct-demo/
RUN if [ "$INCLUDE_DEMO_SEED" = "1" ]; then \
        cp /opt/anyduct-demo/*.sql /docker-entrypoint-initdb.d/ ; \
    fi

LABEL org.opencontainers.image.title="anyduct-postgres" \
      org.opencontainers.image.description="Metadata DB for etl-plugins services with pre-applied schema" \
      org.opencontainers.image.source="https://github.com/duarl/etl-plugins"

HEALTHCHECK --interval=5s --timeout=3s --retries=10 --start-period=10s \
    CMD pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" || exit 1
