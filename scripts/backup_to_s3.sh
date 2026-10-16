#!/usr/bin/env bash
set -euo pipefail
# Run on the EC2 app host using an instance role (no static AWS keys).
# DB credentials are read from the protected systemd EnvironmentFile.
set -a
source /etc/student-task-manager/app.env
set +a

: "${DB_HOST:?missing DB_HOST}"
: "${DB_USER:?missing DB_USER}"
: "${DB_PASSWORD:?missing DB_PASSWORD}"
: "${BACKUP_BUCKET:?missing BACKUP_BUCKET}"
: "${DB_NAME:=student_tasks}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
MYSQL_PWD="$DB_PASSWORD" mysqldump \
  --host="$DB_HOST" --port="${DB_PORT:-3306}" --user="$DB_USER" \
  --single-transaction --quick --routines --triggers "$DB_NAME" > "$tmp"

aws s3 cp "$tmp" "s3://${BACKUP_BUCKET}/mysql/${DB_NAME}-${timestamp}.sql" \
  --sse AES256 --only-show-errors
echo "Backup uploaded: ${DB_NAME}-${timestamp}.sql"
