FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    TZ=Asia/Tashkent

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
        "aiogram>=3.13,<4" \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "python-dotenv>=1.0"

COPY src ./src
COPY web ./web

EXPOSE 8080
CMD ["uvicorn", "schoolhelper.app:app", "--host", "0.0.0.0", "--port", "8080"]
