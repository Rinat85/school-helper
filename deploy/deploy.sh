#!/usr/bin/env bash
#
# Выкатка на VPS. Запускается на сервере — GitHub Actions подаёт этот файл
# на stdin, поэтому скрипт всегда той же версии, что и выкатываемый коммит.
#
# Образ здесь НЕ собирается: на этой машине 1.9 ГБ памяти на все проекты,
# и сборка вытесняла бы работающие сервисы в своп. Образ приезжает готовым
# из GHCR, собранный в GitHub Actions.
#
# Вручную с сервера:  IMAGE_TAG=latest DEPLOY_SHA=origin/main bash deploy/deploy.sh
#
# Что делает: снимок базы -> обновление compose-файлов -> docker compose pull ->
# запуск -> проверка /health -> откат на предыдущий образ, если health не поднялся.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/school-helper}"
TARGET="${DEPLOY_SHA:-origin/main}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/health}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"
KEEP_BACKUPS="${KEEP_BACKUPS:-20}"
TAG_FILE="data/.deployed_tag"

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
        echo "ВНИМАНИЕ: нет sqlite3, снимок базы пропущен (sudo apt install sqlite3)" >&2
    fi
    ls -1t data/backups/school-*.db 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) \
        | xargs -r rm --
fi

# ── 2. Обновление репозитория ───────────────────────────────────────────
# Код в образе, но compose-файл и сам этот скрипт берутся отсюда.
PREVIOUS_SHA="$(git rev-parse HEAD)"
PREVIOUS_TAG="$(cat "$TAG_FILE" 2>/dev/null || echo 'latest')"
git fetch --prune origin
git reset --hard "$TARGET"
log "коммит $(git rev-parse --short "$PREVIOUS_SHA") -> $(git rev-parse --short HEAD)"
log "образ ${PREVIOUS_TAG:0:7} -> ${IMAGE_TAG:0:7}"

# ── 3. Загрузка образа и запуск ─────────────────────────────────────────
start_with() {
    IMAGE_TAG="$1" docker compose pull --quiet
    IMAGE_TAG="$1" docker compose up -d --remove-orphans
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

log "загрузка образа и запуск"
start_with "$IMAGE_TAG"

log "проверка $HEALTH_URL"
if health_ok; then
    curl -fsS "$HEALTH_URL"; echo
    printf '%s\n' "$IMAGE_TAG" > "$TAG_FILE"
    # Старые слои копятся и съедают диск, а места на этой машине немного.
    docker image prune -f --filter 'until=168h' >/dev/null 2>&1 || true
    log "готово"
    exit 0
fi

# ── 4. Откат ────────────────────────────────────────────────────────────
echo "ОШИБКА: health не поднялся за ${HEALTH_TIMEOUT}с, откатываюсь" >&2
docker compose logs --tail 60 || true

git reset --hard "$PREVIOUS_SHA"
start_with "$PREVIOUS_TAG"

if health_ok; then
    echo "откат на образ ${PREVIOUS_TAG:0:7} успешен, бот работает" >&2
else
    echo "КРИТИЧНО: откат тоже не поднялся — нужен руками" >&2
fi
exit 1
