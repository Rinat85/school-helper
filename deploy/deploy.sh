#!/usr/bin/env bash
#
# Выкатка на VPS. Запускается на сервере — GitHub Actions подаёт этот файл
# на stdin, поэтому скрипт всегда той же версии, что и выкатываемый коммит.
#
# Вручную с сервера:  DEPLOY_SHA=origin/main bash deploy/deploy.sh
#
# Что делает: снимок базы -> переключение на нужный коммит -> пересборка ->
# проверка /health -> откат на предыдущий коммит, если health не поднялся.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/school-helper}"
TARGET="${DEPLOY_SHA:-origin/main}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/health}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"
KEEP_BACKUPS="${KEEP_BACKUPS:-20}"

log() { printf '\n=== %s\n' "$*"; }

cd "$APP_DIR"

if [ ! -f .env ]; then
    echo "ОШИБКА: нет $APP_DIR/.env — деплой без токена бота бессмысленен" >&2
    exit 1
fi

# ── 1. Снимок базы ──────────────────────────────────────────────────────
# Деньги. Снимок делаем ДО того, как что-либо трогаем.
mkdir -p data/backups
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [ -f data/school.db ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        # .backup работает на живой базе, в отличие от простого cp при WAL.
        sqlite3 data/school.db ".backup 'data/backups/school-${STAMP}.db'"
        log "снимок базы: data/backups/school-${STAMP}.db"
    else
        echo "ВНИМАНИЕ: нет sqlite3, снимок базы пропущен (apt install sqlite3)" >&2
    fi
    ls -1t data/backups/school-*.db 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) \
        | xargs -r rm --
fi

# ── 2. Переключение на нужный коммит ────────────────────────────────────
PREVIOUS="$(git rev-parse HEAD)"
git fetch --prune origin
git reset --hard "$TARGET"
CURRENT="$(git rev-parse HEAD)"
log "было $(git rev-parse --short "$PREVIOUS") -> стало $(git rev-parse --short "$CURRENT")"

# ── 3. Сборка и запуск ──────────────────────────────────────────────────
deploy_current() {
    GIT_SHA="$(git rev-parse --short HEAD)" docker compose up -d --build
}

health_ok() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    while [ $SECONDS -lt $deadline ]; do
        if curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null | grep -q '"ok":true'; then
            return 0
        fi
        sleep 3
    done
    return 1
}

log "сборка и запуск"
deploy_current

log "проверка $HEALTH_URL"
if health_ok; then
    curl -fsS "$HEALTH_URL"; echo
    log "готово: $(git rev-parse --short HEAD)"
    exit 0
fi

# ── 4. Откат ────────────────────────────────────────────────────────────
echo "ОШИБКА: health не поднялся за ${HEALTH_TIMEOUT}с, откатываюсь" >&2
docker compose logs --tail 60 || true

git reset --hard "$PREVIOUS"
deploy_current

if health_ok; then
    echo "откат на $(git rev-parse --short HEAD) успешен, бот работает" >&2
else
    echo "КРИТИЧНО: откат тоже не поднялся — нужен руками" >&2
fi
exit 1
