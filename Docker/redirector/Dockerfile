FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        apache2 \
        curl \
        gettext-base \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Enable required Apache modules.
RUN a2enmod proxy proxy_http rewrite headers ssl

# Remove the default site; we ship our own.
RUN a2dissite 000-default || true

COPY apache/decoy.html /var/www/html/index.html
COPY apache/c2stack.conf.template /etc/apache2/sites-available/c2stack.conf.template
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
