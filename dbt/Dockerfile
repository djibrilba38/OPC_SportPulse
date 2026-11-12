
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# Installer Git, requis par certaines commandes dbt
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/app

# Installer dbt avec son adaptateur PostgreSQL
RUN python -m pip install --no-cache-dir "dbt-postgres==1.10.2"

ENTRYPOINT ["dbt"]
