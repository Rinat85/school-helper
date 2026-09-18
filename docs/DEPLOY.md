# Деплой на VPS

```
push в main
   └─ Actions: ruff + pytest
        └─ Actions: docker build -> ghcr.io/rinat85/school-helper:<sha>
             └─ ssh на VPS: deploy.sh -> снимок базы -> docker compose pull -> up -d
                  └─ проверка /health -> при неудаче откат на предыдущий образ
```

Сломанный коммит до сервера не доезжает: `build` зависит от `test`, `deploy` — от `build`.

**Образ собирается в GitHub Actions, а не на сервере.** На этом VPS 1.9 ГБ памяти
на все проекты, и локальная сборка вытесняла бы работающие сервисы в своп.
Сервер только забирает готовый образ — это секунды и почти нулевая нагрузка.

---

## 1. Подготовка сервера (один раз)

```bash
sudo apt update && sudo apt install -y git curl sqlite3      # docker уже стоит
```

`sqlite3` нужен для снимков базы перед выкаткой — без него деплой пройдёт,
но напишет предупреждение и снимок не сделает.

**Проверь архитектуру:**

```bash
uname -m
```

`x86_64` — всё готово. Если `aarch64` (Graviton), в
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) в шаге сборки нужно
добавить `--platform linux/arm64` и включить QEMU — сейчас образ собирается
только под amd64, и на ARM он просто не запустится.

### Пользователь для деплоя

Отдельный пользователь не обязателен: если на сервере уже есть непривилегированный
`ubuntu` (обычный случай на AWS), деплоить можно под ним — тогда в секретах
`DEPLOY_USER=ubuntu`. Главное, чтобы он состоял в группе `docker`:

```bash
docker ps                          # "permission denied" -> строка ниже и перезайти по ssh
sudo usermod -aG docker ubuntu
```

Если такого пользователя нет, завести отдельного:

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy
```

Из-под root деплоить тоже можно, но не нужно: приватный ключ лежит в GitHub,
и компрометация секретов не должна сразу давать root на сервере.

### Клонирование

На сервере нужен сам репозиторий — не ради кода (он внутри образа), а ради
`docker-compose.yml` и `deploy/deploy.sh`. Репозиторий публичный, ключи не нужны:

```bash
sudo mkdir -p /opt/school-helper
sudo chown "$USER:$USER" /opt/school-helper
git clone https://github.com/Rinat85/school-helper.git /opt/school-helper
cd /opt/school-helper
```

### .env на сервере

```bash
cp .env.example .env
nano .env        # BOT_TOKEN, BOT_USERNAME, APP_SECRET, BOOTSTRAP_CHAIR_TG_ID
```

`.env` в `.gitignore`, поэтому `git reset --hard` во время деплоя его не трогает.
То же касается `data/` — база и логи переживают любую выкатку.

> **Не редактируй файлы проекта на сервере.** Деплой делает `git reset --hard` и
> сотрёт правки без предупреждения. Всё меняется через репозиторий, кроме `.env`.

---

## 2. Ключ для GitHub Actions

Пара генерируется **локально**, приватная часть уезжает в секреты GitHub,
публичная — на сервер. Отдельная от твоего личного ключа, чтобы её можно было
отозвать, не трогая остальное.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/school-helper-deploy -N "" -C "github-actions"
ssh-copy-id -i ~/.ssh/school-helper-deploy.pub ubuntu@ВАШ_СЕРВЕР   # или deploy@
ssh-keyscan -p 22 ВАШ_СЕРВЕР                                      # для known_hosts
```

---

## 3. Секреты в GitHub

`Settings → Secrets and variables → Actions → New repository secret`:

| Секрет | Что класть |
|---|---|
| `DEPLOY_SSH_KEY` | содержимое `~/.ssh/school-helper-deploy` (приватный ключ, целиком) |
| `DEPLOY_HOST` | IP или домен сервера |
| `DEPLOY_USER` | пользователь для деплоя: `ubuntu` или `deploy` |
| `DEPLOY_KNOWN_HOSTS` | вывод `ssh-keyscan` |
| `DEPLOY_PORT` | порт SSH, если не 22 (иначе не создавать) |
| `DEPLOY_PATH` | путь, если не `/opt/school-helper` (иначе не создавать) |

Токен для GHCR заводить не нужно: сборка публикует образ встроенным
`GITHUB_TOKEN`, права выданы в самом workflow (`packages: write`).

Workflow использует environment `production` — если завести его в
`Settings → Environments` и включить required reviewers, каждая выкатка будет
ждать твоего подтверждения. Полезно, когда пойдут реальные деньги.

---

## 4. Образ в GHCR (один раз после первой сборки)

После первого прогона workflow пакет появится в
`github.com/Rinat85?tab=packages`. По умолчанию он **приватный**, и сервер не
сможет его забрать без авторизации. Проще всего сделать его публичным:

`Package → Package settings → Change visibility → Public`

Секретов в образе нет: код и так в публичном репозитории, а `.env` подключается
на сервере томом и внутрь образа не попадает.

Если держать пакет приватным принципиально — на сервере понадобится вход
с personal access token (scope `read:packages`):

```bash
echo ВАШ_PAT | docker login ghcr.io -u Rinat85 --password-stdin
```

Логин сохранится в `~/.docker/config.json`, и `docker compose pull` заработает.

---

## 5. Как выкатывать

- **обычно** — просто `git push`: тесты, сборка и деплой пройдут сами;
- **вручную** — вкладка Actions → «CI и деплой» → Run workflow;
- **с сервера**, если GitHub Actions недоступен:

```bash
cd /opt/school-helper && IMAGE_TAG=latest DEPLOY_SHA=origin/main bash deploy/deploy.sh
```

### Откат

Автоматический откат срабатывает сам, если `/health` не поднялся: скрипт помнит
предыдущий тег в `data/.deployed_tag` и возвращает его. Руками — любым сохранённым
хешем коммита, теги образов совпадают с ними:

```bash
cd /opt/school-helper
IMAGE_TAG=<полный хеш коммита> DEPLOY_SHA=<он же> bash deploy/deploy.sh
```

### Посмотреть, что крутится

```bash
curl -s localhost:8080/health          # revision = короткий хеш коммита
docker compose logs -f --tail 100
docker stats --no-stream school-helper
```

---

## 6. Домен и Mini App (этап 2)

Пока бот работает опросом, домен не нужен. Он понадобится для Mini App:
Telegram открывает Web App только по HTTPS.

Caddyfile:

```
class.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Затем в `.env` на сервере:

```
PUBLIC_URL=https://class.example.com
WEBHOOK_SECRET=<python -c "import secrets;print(secrets.token_urlsafe(32))">
```

`docker compose up -d` — приложение само зарегистрирует вебхук и переключится
в режим `webhook` (видно в `/health`). Опрос при этом выключается: два режима
одновременно Telegram не разрешает.

Останется привязать Mini App к боту у @BotFather: `/newapp` либо
`/setmenubutton` с адресом `https://class.example.com`.

---

## 7. Ресурсы

Контейнеру выставлен `mem_limit: 256m`. Это не оптимизация, а страховка соседей:
на машине 1.9 ГБ на все проекты, и одна протечка памяти кладёт остальные сервисы
в своп. Если бот упрётся в лимит, его прибьёт и перезапустит — но Spreadis и
торговый бот при этом не пострадают.

Проверить, сколько он реально ест:

```bash
docker stats --no-stream school-helper
```

---

## 8. База

Снимок делается автоматически перед каждой выкаткой в `data/backups/`,
хранятся последние 20. Этого достаточно для откатов, но **это не резервное
копирование**: снимки лежат на том же диске, что и сама база. Когда в кассе
появятся реальные деньги, нужно отдельно настроить выгрузку на другой хост.

Схема применяется при каждом старте (`db.migrate()`), все `CREATE` идут с
`IF NOT EXISTS`, так что повторный запуск безопасен. **Изменения существующих
таблиц** (новая колонка, смена типа) так не приедут — под них нужны настоящие
миграции, их пока нет. Первое же такое изменение придётся делать вместе с
механизмом миграций.
