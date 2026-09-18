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
COPY web ./web

EXPOSE 8080

# Режим (опрос или вебхук) выбирается сам по PUBLIC_URL — см. app.lifespan.
CMD ["uvicorn", "schoolhelper.app:app", "--host", "0.0.0.0", "--port", "8080"]
