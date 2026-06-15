#!/usr/bin/env bash
# Regenerate `services/anyduct-postgres/init/00-schema.sql` +
# `01-alembic-head.sql` by running Alembic against a throwaway postgres
# container and pg_dump'ing the result.
#
# Run this whenever a new Alembic revision is merged. The output files
# are committed to the repo so the bundled `anyduct-postgres` image build
# is reproducible without needing Alembic at image-build time.
#
# Usage:
#     services/anyduct-postgres/regen-schema.sh
#     # or via Makefile:
#     make seed-schema

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INIT_DIR="$SCRIPT_DIR/init"
COMPOSE_FILE="$REPO_ROOT/services/docker-compose.prod.yml"

mkdir -p "$INIT_DIR"

# Compose interpolates these even though they don't matter for the
# metadata-db + anyduct-migrate services we're using here. Dummy values
# are fine because the migrate container reads JWT keys only when the
# FastAPI app boots, which doesn't happen during `db upgrade head`.
export AUTH_JWT_PRIVATE_KEY_PEM="dummy-not-used-during-migrate"   # pragma: allowlist secret
export AUTH_JWT_PUBLIC_KEY_PEM="dummy-not-used-during-migrate"    # pragma: allowlist secret

cleanup() {
    docker compose -f "$COMPOSE_FILE" down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "▶ bringing up metadata-db + anyduct-migrate"
docker compose -f "$COMPOSE_FILE" up -d metadata-db anyduct-migrate

# Wait for anyduct-migrate to exit 0
echo "▶ waiting for migrate to complete …"
for _ in $(seq 1 60); do
    status="$(docker inspect -f '{{.State.Status}}:{{.State.ExitCode}}' services-anyduct-migrate-1 2>/dev/null || echo missing:?)"
    if [[ "$status" == "exited:0" ]]; then
        echo "  ✓ migrate exit 0"
        break
    fi
    if [[ "$status" == exited:* ]] && [[ "$status" != "exited:0" ]]; then
        echo "  ✗ migrate failed: $status" >&2
        docker compose -f "$COMPOSE_FILE" logs anyduct-migrate
        exit 1
    fi
    sleep 1
done

# pg_dump emits \restrict/\unrestrict meta-commands with random tokens
# in pg16+; strip them so the dumped SQL stays diff-stable across runs.
strip_restrict() {
    grep -v -E '^\\(restrict|unrestrict) '
}

echo "▶ dumping schema → $INIT_DIR/00-schema.sql"
docker compose -f "$COMPOSE_FILE" exec -T metadata-db \
    pg_dump -U anyduct --schema-only --no-owner --no-privileges anyduct \
    | strip_restrict > "$INIT_DIR/00-schema.sql"

echo "▶ dumping alembic_version → $INIT_DIR/01-alembic-head.sql"
docker compose -f "$COMPOSE_FILE" exec -T metadata-db \
    pg_dump -U anyduct --data-only --no-owner --no-privileges -t alembic_version anyduct \
    | strip_restrict > "$INIT_DIR/01-alembic-head.sql"

echo "✓ regenerated:"
wc -l "$INIT_DIR"/0*.sql

cat <<EOF

Next steps:
  1. Inspect the diff: git diff $INIT_DIR
  2. Commit the change with a [bundled-db] tag
  3. Rebuild the bundled image:
       docker build -f services/anyduct-postgres/Dockerfile \\
                    -t anyduct-postgres:dev services/anyduct-postgres/
EOF
