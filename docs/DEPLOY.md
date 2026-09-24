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

## 6. Домен и Mini App — `class.spreadis.live`

Telegram открывает Mini App только по `https://`. Порты 80/443 на сервере держит
контейнер Caddy от Spreadis, поэтому своего Caddy мы не ставим: подключаемся к его
docker-сети и добавляем в его Caddyfile один блок.

**Порядок важен.** Если прописать `PUBLIC_URL` раньше, чем заработает HTTPS,
бот зарегистрирует вебхук на адрес, который Telegram не может открыть, и
перестанет получать сообщения. Поэтому сначала маршрут, потом переключение.

### 6.1. DNS

A-запись `class` → публичный IPv4 сервера — там же и так же, как уже сделаны
`api` и `admin` для spreadis.live. Если DNS в Cloudflare — с серым облаком
(DNS only), как у остальных поддоменов: сертификат выпускает Caddy.

Проверка с любого компьютера: `nslookup class.spreadis.live` — должен вернуть IP сервера.

### 6.2. Подключить бота к сети Caddy

На сервере:

```bash
docker network ls | grep spreadis
```

Нужна сеть, в которой сидит `spreadis-caddy` (обычно `<папка-проекта>_spreadis`).
Проверить: `docker network inspect <имя> | grep spreadis-caddy`.

В `/opt/school-helper/.env` добавить:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.proxy.yml
PROXY_NETWORK=<имя сети>
```

и перезапустить: `cd /opt/school-helper && docker compose up -d`.

### 6.3. Блок в Caddyfile Spreadis

В репозитории `spreadis-deploy`, файл `Caddyfile`, в конец:

```
# Родительский комитет: Mini App + вебхук Telegram
class.spreadis.live {
	encode zstd gzip
	header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
	header X-Content-Type-Options "nosniff"
	header Referrer-Policy "strict-origin-when-cross-origin"
	header -Server
	# Без X-Frame-Options, в отличие от остальных блоков: Telegram Web
	# и десктоп открывают Mini App во фрейме, DENY его сломает.
	reverse_proxy school-helper:8080
}
```

Выкатить Spreadis как обычно (push в `spreadis-deploy`). Caddy сам получит сертификат.

Проверка:

```bash
curl -s https://class.spreadis.live/health; echo
```

Должен вернуться тот же JSON, что и с `localhost:8080/health`. Пока это не так —
к шагу 6.4 не переходить.

### 6.4. Переключить бота на вебхук

В `/opt/school-helper/.env`:

```
PUBLIC_URL=https://class.spreadis.live
WEBHOOK_SECRET=<python3 -c "import secrets;print(secrets.token_urlsafe(32))">
```

`docker compose up -d`. Дальше всё само:

- бот регистрирует вебхук и переключается в режим `webhook` (видно в `/health`);
- у бота в личке появляется кнопка меню **«Класс»**, открывающая Mini App.

Опрос при этом выключается: два режима одновременно Telegram не разрешает.

### 6.5. Ссылка на Mini App для группы

В группах Telegram не разрешает кнопки, открывающие Web App напрямую. Чтобы
давать в группу ссылку вида `t.me/<бот>?startapp`, у @BotFather:
Bot Settings → **Configure Mini App** (или Mini Apps → Main App) → URL
`https://class.spreadis.live`. Необязательно — кнопка меню в личке работает и без этого.

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

При каждом старте (`db.migrate()`) применяется исходная схема `schema.sql`,
а затем недостающие миграции из `src/schoolhelper/storage/migrations.py`.
Каждая миграция — в своей транзакции: если упала, база остаётся как была,
а контейнер не проходит `/health` и деплой откатывается на прежний образ.
Какие миграции выполнены — в таблице `schema_migration`:

```bash
sqlite3 data/school.db "SELECT * FROM schema_migration"
```

`schema.sql` заморожен: изменения существующих таблиц и новые таблицы —
только новой миграцией. Если миграция всё-таки испортила данные, база до неё
лежит в `data/backups/` — снимок делается перед каждой выкаткой.
