FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system sportpulse && adduser --system --ingroup sportpulse sportpulse
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./pyproject.toml
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && mkdir -p /app/data/source /app/data/manifests \
    && chown -R sportpulse:sportpulse /app

USER sportpulse
EXPOSE 8000
CMD ["uvicorn", "sportpulse.api:app", "--host", "0.0.0.0", "--port", "8000"]
