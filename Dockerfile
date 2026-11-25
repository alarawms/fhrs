FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY registry ./registry
COPY countries ./countries
COPY migrations ./migrations
COPY alembic.ini ./
COPY web ./web

RUN pip install --no-cache-dir .

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# N9: the entrypoint never swallows commands — any argument other than "serve"
# is exec'd verbatim (harvest CLIs, doctor, alembic, a shell, ...).
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["serve"]
