# Deploying Poultry IQ to Namecheap shared hosting

Every push to `main` is checked by GitHub Actions. If the checks pass, the
workflow connects to Namecheap over SSH, updates the server to exactly
`origin/main`, installs dependencies, applies migrations, collects static files,
and restarts Passenger.

For MariaDB deployments, the script also creates a consistent compressed SQL
dump under `/home/CPANEL_USER/poultryiq-data/backups` before applying migrations.
Review that directory periodically so old backups do not exhaust the account's
storage quota.

The commands below use these placeholders:

- `CPANEL_USER`: your cPanel username
- `DOMAIN`: the domain or subdomain that will host Poultry IQ
- `premium99.web-hosting.com`: confirm this full hostname in the Namecheap
  welcome email or cPanel's **Server Information** page

## 1. Point the domain to the hosting account

Add the domain or subdomain in cPanel under **Domains**. If the domain uses
Namecheap hosting nameservers, cPanel manages the DNS record. Otherwise, point
the domain's `A` record to the shared-hosting IP shown in cPanel.

Wait for DNS to resolve, then enable SSL in cPanel. Do not enable
`DJANGO_SECURE_SSL_REDIRECT` until `https://DOMAIN` works.

## 2. Enable and test SSH

In cPanel, open **SSH Access** and enable shell access. Namecheap shared hosting
uses port `21098`.

From your computer, test:

```bash
ssh CPANEL_USER@premium99.web-hosting.com -p 21098
```

If shell access cannot be enabled in cPanel, ask Namecheap Hosting Support to
enable it for the account.

## 3. Put the repository on the server

Open cPanel **Terminal** (or use the SSH session) and run:

```bash
cd "$HOME"
git clone --branch MarcDemoEdits https://github.com/MarcDemo/POULTRYIQ.git poultryiq
mkdir -p "$HOME/poultryiq-data/media"
chmod 700 "$HOME/poultryiq-data"
```

The HTTPS clone works when the repository is public. For a private repository,
create a separate read-only GitHub deploy key on the server, add its public half
under the repository's **Settings > Deploy keys**, then clone with the SSH URL.
Do not put a GitHub token in the repository URL.

`MarcDemoEdits` is the one-time bootstrap branch containing this deployment
setup. Normal automatic deployments switch the server to `main` after it is
merged.

## 4. Create the cPanel Python application

In cPanel, open **Setup Python App > Create Application** and enter:

- Python version: `3.13`
- Application root: `poultryiq`
- Application URL: select `DOMAIN`
- Application startup file: `passenger_wsgi.py`
- Application entry point: `application`

Create the application. Copy the virtual-environment command cPanel displays;
it reveals the generated environment path needed below. The Python executable
will look similar to:

```text
/home/CPANEL_USER/virtualenv/poultryiq/3.13/bin/python
```

Shared hosting runs WSGI/Passenger, not ASGI. This repository's
`passenger_wsgi.py` is already configured for that.

## 5. Create the MariaDB database

In cPanel, open **Database Wizard** and create:

- Database: `poultryiq`
- Database user: `poultryiq`
- Password: use cPanel's password generator and save the result securely
- Privileges: grant **All Privileges** to that user on the database

cPanel prefixes both names with the account username. For example, if the
cPanel username is `farmadmin`, the final values may be
`farmadmin_poultryiq`. Copy the exact names displayed by cPanel.

The application connects locally through `127.0.0.1:3306`; do not enable public
remote database access.

## 6. Create the server-only production settings

Generate a secret key in cPanel Terminal:

```bash
python3 -c "from secrets import token_urlsafe; print(token_urlsafe(64))"
```

Create `/home/CPANEL_USER/poultryiq/.env.production` with cPanel File Manager.
Copy `.env.example` and replace every placeholder:

```dotenv
DJANGO_SECRET_KEY=PASTE_THE_GENERATED_SECRET
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=DOMAIN,www.DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=https://DOMAIN,https://www.DOMAIN
DJANGO_DB_ENGINE=mariadb
DJANGO_DB_NAME=CPANEL_USER_poultryiq
DJANGO_DB_USER=CPANEL_USER_poultryiq
DJANGO_DB_PASSWORD=PASTE_THE_CPANEL_DATABASE_PASSWORD
DJANGO_DB_HOST=127.0.0.1
DJANGO_DB_PORT=3306
DJANGO_DB_CONN_MAX_AGE=60
DJANGO_DB_BACKUP_DIR=/home/CPANEL_USER/poultryiq-data/backups
DJANGO_MEDIA_ROOT=/home/CPANEL_USER/poultryiq-data/media
DJANGO_STATIC_ROOT=/home/CPANEL_USER/poultryiq/staticfiles
DJANGO_SECURE_SSL_REDIRECT=False
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
URA_TIN_LOOKUP_URL=
URA_TIN_LOOKUP_TOKEN=
```

Set its permission to `0600` in File Manager or Terminal:

```bash
chmod 600 "$HOME/poultryiq/.env.production"
```

After HTTPS is confirmed, change `DJANGO_SECURE_SSL_REDIRECT` to `True`.

For a fresh database, the first deployment creates all tables. An existing
SQLite database cannot simply be uploaded as MariaDB. Export its Django data to
JSON, deploy the empty MariaDB schema, and then import that JSON separately.
Upload the contents of the local `media` directory to
`/home/CPANEL_USER/poultryiq-data/media` before the first deployment if those
files must be retained.

## 7. Expose uploaded media

Static CSS and JavaScript are served by WhiteNoise. Uploaded media is not; it
must be served directly by the web server. Find `DOMAIN`'s document root in
cPanel **Domains**, then create a `media` symbolic link there. For a document
root of `/home/CPANEL_USER/public_html`, run:

```bash
ln -s "$HOME/poultryiq-data/media" "$HOME/public_html/media"
```

If `media` already exists, do not overwrite it. Move its contents into the
persistent media directory first, or ask Namecheap Support to map `/media/` to
that directory. For stronger isolation in a later phase, move uploads to object
storage on a separate media domain.

## 8. Run the first deployment manually

Use the exact Python path shown by **Setup Python App**:

```bash
bash "$HOME/poultryiq/scripts/deploy_namecheap.sh" \
  "$HOME/poultryiq" \
  "/home/CPANEL_USER/virtualenv/poultryiq/3.13/bin/python" \
  "MarcDemoEdits"
```

Open `https://DOMAIN`, the admin login, a page with CSS, and one uploaded image.
If anything fails, check **Setup Python App > Log file** before enabling the
automatic workflow.

The script refuses to migrate MariaDB unless `mariadb-dump` or `mysqldump` can
create a backup first.

## 9. Create a dedicated GitHub Actions SSH key

In cPanel **SSH Access > Manage SSH Keys**, generate a 4096-bit RSA key named
`github-actions-deploy`, leave the passphrase empty, and authorize its public
key. Download the private key in OpenSSH/PEM format. This key grants shell access,
so keep it only in GitHub Secrets and revoke it if exposed.

On a trusted computer, capture the host key:

```bash
ssh-keyscan -p 21098 premium99.web-hosting.com
```

Verify its fingerprint against the key shown by your first trusted SSH login or
with Namecheap Support. Do not blindly trust an unverified scan.

## 10. Configure GitHub's production environment

In the GitHub repository, open **Settings > Environments**, create
`production`, and allow deployments only from `main`.

Add these environment variables:

- `NAMECHEAP_HOST`: `premium99.web-hosting.com` (after confirming it)
- `NAMECHEAP_PORT`: `21098`
- `NAMECHEAP_USER`: your cPanel username
- `NAMECHEAP_APP_PATH`: `/home/CPANEL_USER/poultryiq`
- `NAMECHEAP_VENV_PYTHON`: the exact cPanel virtualenv Python path
- `PRODUCTION_URL`: `https://DOMAIN`

Add these environment secrets:

- `NAMECHEAP_SSH_PRIVATE_KEY`: the entire private deployment key, including its
  BEGIN and END lines
- `NAMECHEAP_KNOWN_HOSTS`: the verified full `ssh-keyscan` output

## 11. Merge and verify

Push this branch, open a pull request into `main`, and let the checks run. When
the PR is merged, **Actions > Verify and deploy production** should show:

1. dependency installation;
2. Django and migration checks;
3. SSH setup;
4. the server deployment ending with `Deployed <commit> successfully.`

The current application test suite has pre-existing failures, so the deployment
gate currently runs Django's system and migration checks. Repair the tests, then
add `python manage.py test` back to the workflow before treating it as a full CI
quality gate.

## Rollback

In cPanel Terminal, find a known-good commit and deploy it temporarily:

```bash
cd "$HOME/poultryiq"
git log --oneline -10
git checkout KNOWN_GOOD_COMMIT
/home/CPANEL_USER/virtualenv/poultryiq/3.13/bin/python manage.py collectstatic --noinput
mkdir -p tmp && touch tmp/restart.txt
```

Do not reverse database migrations unless you have inspected them and restored a
database backup. The next successful merge to `main` returns the server to the
latest main commit.
