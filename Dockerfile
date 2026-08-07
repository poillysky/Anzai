# Combined Anzai image: FastAPI (8515) + Next.js PWA (3515)
# Build from repo root: docker build -t poillysky/anzai:1.0.1 .

FROM node:20-bookworm-slim AS web-deps
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM node:20-bookworm-slim AS web-build
WORKDIR /web
COPY --from=web-deps /web/node_modules ./node_modules
COPY apps/web/ ./
ENV NEXT_TELEMETRY_DISABLED=1
ENV API_PROXY_TARGET=http://127.0.0.1:8515
ENV NEXT_PUBLIC_API_BASE=/backend
RUN npm run build

FROM node:20-bookworm-slim AS runtime
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
  && rm -rf /var/lib/apt/lists/* \
  && ln -sf /usr/bin/python3 /usr/local/bin/python

WORKDIR /app

COPY apps/api/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
  && rm /tmp/requirements.txt

COPY apps/api/ /app/api/
# Drop local junk if copied
RUN rm -rf /app/api/.venv /app/api/__pycache__ /app/api/*.db /app/api/data || true

COPY --from=web-build /web/public /app/web/public
COPY --from=web-build /web/.next/standalone /app/web/
COPY --from=web-build /web/.next/static /app/web/.next/static

COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    API_PROXY_TARGET=http://127.0.0.1:8515 \
    NEXT_PUBLIC_API_BASE=/backend \
    PORT=3515 \
    HOSTNAME=0.0.0.0 \
    DATABASE_URL=sqlite:////app/data/anzai.db

WORKDIR /app
EXPOSE 3515 8515
ENTRYPOINT ["/entrypoint.sh"]
