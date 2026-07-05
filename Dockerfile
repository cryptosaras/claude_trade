FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY strategies ./strategies
COPY config ./config
COPY ui ./ui
ENV APP_ROOT=/srv PYTHONUNBUFFERED=1
