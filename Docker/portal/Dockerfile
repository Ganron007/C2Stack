FROM python:3.12-slim

WORKDIR /app

# The docker CLI is required: c2backends.py shells out to `docker exec` to run
# psql inside the Mythic postgres container and the Meridian CLI for tasking.
# The read-only socket bind alone is not enough - /api/ops/* endpoints fail
# without the binary present.
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ca-certificates \
        curl \
        openssh-client \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz" \
        -o /tmp/docker.tgz \
    && tar xzf /tmp/docker.tgz -C /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker /tmp/docker.tgz \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py c2backends.py adaptix_client.py havoc_client.py sliver_client.py ./
COPY static/ ./static/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
