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

# NOTE: the build context is Docker/ (see docker-compose.yml) because the
# config renderer needs the redirector + Havoc templates from sibling
# directories. All paths below are therefore relative to Docker/.
COPY portal/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# labconfig.py + lagrender.py MUST be listed here: they are the settings store
# and the config renderer the Lab Config tab drives. Omitting either produced a
# hard ModuleNotFoundError at boot, which takes the whole portal (every tab)
# down - not a degraded feature.
COPY portal/app.py portal/c2backends.py portal/adaptix_client.py \
     portal/havoc_client.py portal/sliver_client.py \
     portal/labconfig.py portal/lagrender.py ./
COPY portal/static/ ./static/

# Templates the Lab Config tab renders from. The portal is the single writer
# of the rendered output in /render; the consumers mount that read-only.
# These stay as build context so the repo copy remains the source of truth -
# render_all() reads them from /render/templates.
RUN mkdir -p /render/templates
COPY redirector/apache/c2stack.conf.template /render/templates/
COPY havoc/havoc.yaotl /render/templates/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
