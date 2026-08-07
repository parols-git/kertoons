#!/usr/bin/env bash
# ==============================================================================
# Kertoons "school bubble" provisioner
#
# Run this ONCE, as root, on a FRESH Ubuntu DigitalOcean droplet you've
# already created for one school - it takes that empty droplet all the way
# to a live, HTTPS, MySQL-backed Kertoons site at the school's own domain.
# This follows the exact same steps as DEPLOY.md's standalone-domain path
# (nginx, not Apache - see DEPLOY.md's "Mounting under an existing site"
# section for why: Apache is only for retrofitting onto a site something
# else already occupies, which a dedicated per-school droplet never is),
# just automated end to end and parameterized per school.
#
# WHAT IT DOES (in order):
#   1. Installs the stack: Python, MySQL, nginx, ffmpeg, certbot, git.
#   2. Adds a 2GB swapfile - a 1GB droplet WILL eventually OOM-kill MySQL
#      without this (not theoretical: it happened on the very first real
#      deployment this script is based on).
#   3. Creates a dedicated, unprivileged system user to run the app - never
#      root - matching deploy/kertoons.service's expectations.
#   4. Clones the app straight to /opt/kertoons-app (the repo root IS the
#      app root - no extra nesting), creates its venv, installs deps.
#   5. Creates this school's own MySQL database + a MySQL user scoped ONLY
#      to that database (never root, never shared with any other school).
#   6. Points the app straight at MySQL from first boot - a brand-new
#      school has no prior JSON data, so there's nothing to migrate.
#   7. Writes .env (PORT=8765, HOST=127.0.0.1, PUBLIC_BASE_URL, admin
#      bootstrap credentials, optionally carried-over API keys) and
#      installs deploy/kertoons.service as-is.
#   8. Installs deploy/nginx_kertoons.conf with this school's domain,
#      then runs certbot --nginx to get a real Let's Encrypt cert and
#      force HTTPS - exactly DEPLOY.md step 9, automated.
#   9. Locks down ufw: SSH + HTTP/HTTPS only. MySQL is never exposed to
#      the internet - the app reaches it over localhost only.
#  10. Seeds the school's name into site settings and prints every
#      credential you'll need, once, at the end. Nothing is logged to disk.
#
# PREREQUISITE: the domain's DNS A record must ALREADY point at this
# droplet's public IP before you run this script - certbot's domain
# validation will fail otherwise.
#
# USAGE:
#   sudo bash provision_school.sh "Lincoln Academy" lincoln-academy.com
#   sudo bash provision_school.sh "Lincoln Academy" lincoln-academy.com --env-template ./shared_api_keys.env
#
# The optional --env-template file lets you carry over API keys (OpenAI,
# Gemini, DeepAI, Stripe) from an existing deployment instead of this school
# starting in mock mode. Prepare it yourself first, e.g.:
#   ssh root@main-server "grep -E '^(OPENAI|GEMINI|DEEPAI|STRIPE)_' /opt/kertoons-app/.env" > shared_api_keys.env
# Only those API-key lines are pulled from it - everything instance-specific
# (DB credentials, admin password, domain) is always generated fresh per
# school, never copied, so schools can never end up sharing credentials.
# Keys can also be added later, per school, from Admin > API Keys.
# ==============================================================================
set -euo pipefail

# ------------------------------------------------------------------- args
if [ $# -lt 2 ]; then
  echo "Usage: $0 \"<School Name>\" <domain> [--env-template <file>]" >&2
  exit 1
fi

SCHOOL_NAME="$1"
DOMAIN="$2"
shift 2
ENV_TEMPLATE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env-template)
      ENV_TEMPLATE="${2:?--env-template needs a file path}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root (e.g. sudo bash $0 ...)." >&2
  exit 1
fi

# ---------------------------------------------------------------- config
REPO_URL="https://github.com/parols-git/kertoons.git"
APP_DIR="/opt/kertoons-app"
SERVICE_USER="kertoons"
APP_PORT=8765

ADMIN_USERNAME="admin"

# Slug for DB names: lowercase, non-alnum -> underscore, capped length so
# it fits MySQL's identifier limits comfortably.
SCHOOL_SLUG=$(echo "$SCHOOL_NAME" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '_' | sed 's/_\+/_/g; s/^_//; s/_$//' | cut -c1-24)
DB_NAME="kt_${SCHOOL_SLUG}"
DB_USER="kt_${SCHOOL_SLUG}_app"

ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '=+/')
DB_PASSWORD=$(openssl rand -base64 24 | tr -d '=+/')

echo "==> Provisioning '$SCHOOL_NAME' at https://$DOMAIN"
echo "==> DB name: $DB_NAME   DB user: $DB_USER   App dir: $APP_DIR"

# ------------------------------------------------------- 1. system packages
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  python3 python3-venv python3-pip \
  ffmpeg \
  nginx \
  mysql-server \
  certbot python3-certbot-nginx \
  git curl ufw

# ------------------------------------------------------------- 2. swapfile
# Without this, MySQL gets OOM-killed under load on any droplet with 1-2GB
# RAM - the exact failure this provisioner exists to prevent from repeating.
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "==> Added 2GB swapfile"
else
  echo "==> Swapfile already present, skipping"
fi

# ---------------------------------------------------- 3. service user
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$SERVICE_USER"
fi

# --------------------------------------------------- 4. app code + venv
# Repo root IS the app root (server.py lives at the top level) - matches
# DEPLOY.md step 4 exactly, just run non-interactively as the service user.
if [ ! -d "$APP_DIR/.git" ]; then
  sudo -u "$SERVICE_USER" git clone --depth 1 "$REPO_URL" "$APP_DIR"
else
  sudo -u "$SERVICE_USER" git -C "$APP_DIR" pull --ff-only
fi

sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# ------------------------------------------------- 5. MySQL: db + scoped user
# Uses the default post-install root auth (unix_socket via `mysql` as root) -
# no separate root-password dance needed on a fresh mysql-server install.
mysql -e "CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -e "CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';"
mysql -e "GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';"
mysql -e "FLUSH PRIVILEGES;"
echo "==> Created MySQL database and scoped user (localhost-only, no other school's data reachable)"

# --------------------------------------------- 6. point the app at MySQL
# A brand-new school has no prior JSON data, so this skips the JSON->MySQL
# migration path entirely - the app boots straight onto MySQL. Schema
# creation (ensure_schema) runs from inside the app package so it always
# matches whatever version of the schema this checkout actually has.
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/python" -c "
import sys
sys.path.insert(0, '$APP_DIR')
from story_engine import mysql_store, backend_config

settings = {
    'host': 'localhost', 'port': 3306,
    'database': '${DB_NAME}', 'user': '${DB_USER}', 'password': '${DB_PASSWORD}',
}
mysql_store.ensure_schema(settings)
backend_config.set_mysql_settings(**settings)
backend_config.set_migrated()   # nothing to migrate - a fresh school starts empty
backend_config.set_backend('mysql')
print('MySQL schema ready, backend set to mysql')
"

# ------------------------------------------------------------- 7. .env
ENV_FILE="$APP_DIR/.env"
{
  echo "HOST=127.0.0.1"
  echo "PORT=${APP_PORT}"
  echo "PUBLIC_BASE_URL=https://${DOMAIN}"
  echo "ADMIN_USERNAME=${ADMIN_USERNAME}"
  echo "ADMIN_PASSWORD=${ADMIN_PASSWORD}"
  if [ -n "$ENV_TEMPLATE" ] && [ -f "$ENV_TEMPLATE" ]; then
    grep -E '^(OPENAI|GEMINI|DEEPAI|STRIPE)_[A-Z_]+=' "$ENV_TEMPLATE" || true
  fi
} > "$ENV_FILE"
chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"
if [ -n "$ENV_TEMPLATE" ] && [ -f "$ENV_TEMPLATE" ]; then
  echo "==> Copied API keys from $ENV_TEMPLATE into .env"
else
  echo "==> No --env-template given - app starts in mock mode until API keys are added"
  echo "    (either edit $ENV_FILE by hand, or use the admin panel's API Keys section)"
fi

# --------------------------------------------------------- 8. systemd unit
cp "$APP_DIR/deploy/kertoons.service" /etc/systemd/system/kertoons.service
systemctl daemon-reload
systemctl enable --now kertoons
sleep 2
if ! systemctl is-active --quiet kertoons; then
  echo "!! kertoons.service failed to start - check: journalctl -u kertoons -n 50" >&2
  exit 1
fi
echo "==> App service running on 127.0.0.1:${APP_PORT}"

# ---------------------------------------------------------- 9. nginx + SSL
cp "$APP_DIR/deploy/nginx_kertoons.conf" /etc/nginx/sites-available/"${DOMAIN}"
sed -i "s/YOUR_DOMAIN_OR_IP/${DOMAIN} www.${DOMAIN}/" /etc/nginx/sites-available/"${DOMAIN}"
ln -sf /etc/nginx/sites-available/"${DOMAIN}" /etc/nginx/sites-enabled/"${DOMAIN}"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> Requesting Let's Encrypt certificate for ${DOMAIN} (DNS must already point here)..."
certbot --nginx -d "${DOMAIN}" -d "www.${DOMAIN}" \
  --non-interactive --agree-tos -m "admin@${DOMAIN}" --redirect

# ------------------------------------------------------------- 10. firewall
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
echo "==> Firewall: SSH + HTTP/HTTPS only. MySQL is not reachable from outside this box."

# ----------------------------------------------- 11. seed branding + report
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/python" -c "
import sys
sys.path.insert(0, '$APP_DIR')
from story_engine import db
db.set_site_settings(site_name='${SCHOOL_NAME}', footer_text='${SCHOOL_NAME} - powered by Kertoons')
print('Site branding seeded')
"

cat <<EOF

================================================================================
  ${SCHOOL_NAME} is live: https://${DOMAIN}

  Admin login
    URL:      https://${DOMAIN}/login.html
    Username: ${ADMIN_USERNAME}
    Password: ${ADMIN_PASSWORD}

  MySQL (localhost-only, not internet-reachable)
    Database: ${DB_NAME}
    Username: ${DB_USER}
    Password: ${DB_PASSWORD}

  Save these somewhere safe now - they are not written to any log file and
  will not be shown again. Next steps for the school admin: log in, upload
  the school's logo (Admin > Settings), and add API keys if you didn't pass
  --env-template (Admin > API Keys).
================================================================================
EOF
