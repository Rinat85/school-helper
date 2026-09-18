# Деплой на VPS

Схема: push в `main` → GitHub Actions гоняет `ruff` и `pytest` → если зелено, заходит
по SSH на сервер и запускает [`deploy/deploy.sh`](../deploy/deploy.sh), который делает
снимок базы, переключается на нужный коммит, пересобирает контейнер и проверяет
`/health`. Если health не поднялся — автоматический откат на предыдущий коммит.

Сломанный коммит до сервера не доезжает: job `deploy` зависит от job `test`.

---

## 1. Подготовка сервера (один раз)

```bash
sudo apt update && sudo apt install -y git curl sqlite3      # docker уже стоит
```

`sqlite3` нужен для снимков базы перед выкаткой — без него деплой пройдёт,
но напишет предупреждение и снимок не сделает.

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

Репозиторий публичный, поэтому ключи для `git fetch` не нужны — хватит HTTPS.
Владельцем каталога делается тот пользователь, под которым пойдёт деплой:

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

### Первый запуск руками

```bash
GIT_SHA=$(git rev-parse --short HEAD) docker compose up -d --build
curl -s localhost:8080/health
```

Ожидаемо: `{"ok":true,"mode":"polling",...}`. Режим `polling` — это нормально,
пока нет домена: бот работает опросом, HTTP-сервер поднят только ради `/health`.

---

## 2. Ключ для GitHub Actions

Пара генерируется **локально**, приватная часть уезжает в секреты GitHub,
публичная — на сервер. Отдельная от твоего личного ключа, чтобы её можно было
отозвать, не трогая остальное.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/school-helper-deploy -N "" -C "github-actions"
```

Публичную часть — на сервер:

```bash
ssh-copy-id -i ~/.ssh/school-helper-deploy.pub ubuntu@ВАШ_СЕРВЕР   # или deploy@
```

Отпечаток сервера для `known_hosts` (чтобы Actions не принимал хост вслепую):

```bash
ssh-keyscan -p 22 ВАШ_СЕРВЕР
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

Workflow использует environment `production` — если завести его в
`Settings → Environments` и включить required reviewers, каждая выкатка будет
ждать твоего подтверждения. Полезно, когда пойдут реальные деньги.

---

## 4. Как выкатывать

- **обычно** — просто `git push`: тесты и деплой пройдут сами;
- **вручную** — вкладка Actions → «CI и деплой» → Run workflow;
- **с сервера**, если GitHub недоступен:

```bash
cd /opt/school-helper && DEPLOY_SHA=origin/main bash deploy/deploy.sh
```

### Откат

Автоматический откат срабатывает сам, если `/health` не поднялся. Руками:

```bash
cd /opt/school-helper
DEPLOY_SHA=<хеш коммита> bash deploy/deploy.sh
```

### Посмотреть, что крутится

```bash
curl -s localhost:8080/health          # revision = короткий хеш коммита
docker compose logs -f --tail 100
```

---

## 5. Домен и Mini App (этап 2)

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

## 6. База

Снимок делается автоматически перед каждой выкаткой в `data/backups/`,
хранятся последние 20. Этого достаточно для откатов, но **это не резервное
копирование**: снимки лежат на том же диске, что и сама база. Когда в кассе
появятся реальные деньги, нужно отдельно настроить выгрузку на другой хост.

Схема применяется при каждом старте (`db.migrate()`), все `CREATE` идут с
`IF NOT EXISTS`, так что повторный запуск безопасен. **Изменения существующих
таблиц** (новая колонка, смена типа) так не приедут — под них нужны настоящие
миграции, их пока нет. Первое же такое изменение придётся делать вместе с
механизмом миграций.
