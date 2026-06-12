#!/usr/bin/env bash
set -euo pipefail

# Wait for Postgres before starting (the app also create_all's on boot).
echo "Waiting for Postgres..."
python - <<'PY'
import time, os
import psycopg2
from urllib.parse import urlparse
url = os.environ.get("DATABASE_URL", "postgresql+psycopg2://storyforge:storyforge@postgres:5432/storyforge")
url = url.replace("postgresql+psycopg2", "postgresql")
p = urlparse(url)
for _ in range(60):
    try:
        psycopg2.connect(host=p.hostname, port=p.port or 5432, user=p.username,
                         password=p.password, dbname=p.path.lstrip("/")).close()
        print("Postgres is up.")
        break
    except Exception as e:
        print("...waiting:", e)
        time.sleep(1)
else:
    raise SystemExit("Postgres never became ready")
PY

exec "$@"
