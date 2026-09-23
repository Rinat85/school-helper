# ── 1. Mini App: собирается Node'ом, в итоговый образ попадает только статика ──
FROM node:22-alpine AS webapp

WORKDIR /webapp
COPY webapp/package.json webapp/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY webapp/ ./
# vite кладёт сборку в ../web — то есть в /web
RUN npm run build


# ── 2. Бэкенд ────────────────────────────────────────────────────────────
FROM python:3.11-slim

# GIT_SHA проставляется при сборке и виден в /health — так видно,
# какой именно коммит сейчас крутится на сервере.
ARG GIT_SHA=dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    GIT_SHA=${GIT_SHA} \
    TZ=Asia/Tashkent

WORKDIR /app

# tzdata ставим пакетом: в slim-образе системной базы часовых поясов может не быть,
# а без неё ZoneInfo("Asia/Tashkent") падает.
RUN pip install --no-cache-dir \
        "aiogram>=3.13,<4" \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "python-dotenv>=1.0" \
        "tzdata>=2024.1"

COPY src ./src
COPY --from=webapp /web ./web

EXPOSE 8080

# Режим (опрос или вебхук) выбирается сам по PUBLIC_URL — см. app.lifespan.
# X-Forwarded-For от Caddy намеренно не доверяем: адрес клиента нужен только
# dev-входу, и он должен видеть адрес docker-сети, а не заголовок.
CMD ["uvicorn", "schoolhelper.app:app", "--host", "0.0.0.0", "--port", "8080"]
