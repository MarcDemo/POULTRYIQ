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
"$VENV_PYTHON" manage.py migrate --noinput
"$VENV_PYTHON" manage.py collectstatic --noinput

mkdir -p tmp
touch tmp/restart.txt

echo "Deployed $(git rev-parse --short HEAD) successfully."
