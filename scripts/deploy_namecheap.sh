#!/usr/bin/env bash
set -Eeuo pipefail

APP_PATH="${1:?Usage: deploy_namecheap.sh APP_PATH VENV_PYTHON [GIT_BRANCH]}"
VENV_PYTHON="${2:?Usage: deploy_namecheap.sh APP_PATH VENV_PYTHON [GIT_BRANCH]}"
GIT_BRANCH="${3:-main}"

cd "$APP_PATH"

if [[ ! -f .env.production ]]; then
  echo "Missing $APP_PATH/.env.production" >&2
  exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Python executable not found: $VENV_PYTHON" >&2
  exit 1
fi

if ! git check-ref-format --branch "$GIT_BRANCH" >/dev/null 2>&1; then
  echo "Invalid Git branch: $GIT_BRANCH" >&2
  exit 1
fi

# Only committed application code is replaced. Runtime data lives outside the
# repository through DJANGO_DB_PATH and DJANGO_MEDIA_ROOT.
git fetch --prune origin "+refs/heads/$GIT_BRANCH:refs/remotes/origin/$GIT_BRANCH"
git checkout -B "$GIT_BRANCH" "origin/$GIT_BRANCH"
git reset --hard "origin/$GIT_BRANCH"

"$VENV_PYTHON" -m pip install --disable-pip-version-check -r requirements.txt
"$VENV_PYTHON" manage.py check --deploy

"$VENV_PYTHON" <<'PY'
import gzip
import os
from pathlib import Path
import shutil
import subprocess

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "poultryiq.settings")
django.setup()

from django.conf import settings

database = settings.DATABASES["default"]
if database["ENGINE"] in {"django.db.backends.mysql", "mysql.connector.django"}:
    dump_program = shutil.which("mariadb-dump") or shutil.which("mysqldump")
    if not dump_program:
        raise RuntimeError("mariadb-dump or mysqldump is required before migrations")

    from datetime import datetime, timezone

    backup_dir = Path(os.environ["DJANGO_DB_BACKUP_DIR"])
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"mariadb-{timestamp}.sql.gz"
    command = [
        dump_program,
        f"--host={database['HOST']}",
        f"--port={database['PORT']}",
        f"--user={database['USER']}",
        "--single-transaction",
        "--quick",
        "--skip-lock-tables",
        database["NAME"],
    ]
    process_environment = os.environ.copy()
    process_environment["MYSQL_PWD"] = database["PASSWORD"]

    try:
        with gzip.open(backup_path, "wb") as backup_file:
            result = subprocess.run(
                command,
                stdout=backup_file,
                stderr=subprocess.PIPE,
                env=process_environment,
                check=False,
            )
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace"))
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise

    print(f"MariaDB backup created at {backup_path}")
PY

"$VENV_PYTHON" manage.py migrate --noinput
"$VENV_PYTHON" manage.py collectstatic --noinput

mkdir -p tmp
touch tmp/restart.txt

# LiteSpeed on Namecheap can keep existing lswsgi workers alive after the
# Passenger restart marker changes. Stop only workers serving this exact app;
# LiteSpeed starts fresh workers on the next request.
while IFS= read -r worker_pid; do
  worker_command="$(ps -p "$worker_pid" -o args= 2>/dev/null || true)"
  if [[ "$worker_command" == *"lswsgi -m $APP_PATH/passenger_wsgi.py" ]]; then
    kill "$worker_pid" 2>/dev/null || true
  fi
done < <(pgrep -u "$(id -u)" -x lswsgi 2>/dev/null || true)

echo "Deployed $(git rev-parse --short HEAD) successfully."
