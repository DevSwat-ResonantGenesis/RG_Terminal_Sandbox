FROM docker:26-cli AS dockercli

FROM python:3.11-slim

WORKDIR /app

# Only the controller needs the docker CLI - it never runs user code itself,
# it only shells out to `docker run`/`docker exec` against sandbox-image containers.
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY sandbox-image ./sandbox-image

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
