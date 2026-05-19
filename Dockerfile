FROM node:22-bookworm-slim AS frontend-deps
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci

FROM node:22-bookworm-slim AS frontend-build
WORKDIR /app/frontend
ENV BACKEND_URL=http://127.0.0.1:8000
COPY --from=frontend-deps /app/frontend/node_modules ./node_modules
COPY frontend ./
RUN mkdir -p public
RUN npm run build
RUN npm prune --omit=dev

FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    NODE_ENV=production \
    HOST=127.0.0.1 \
    PORT=8000 \
    FRONTEND_HOST=0.0.0.0 \
    FRONTEND_PORT=3000 \
    BACKEND_URL=http://127.0.0.1:8000

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl libstdc++6 \
  && rm -rf /var/lib/apt/lists/*

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

COPY backend ./backend
COPY --from=frontend-build /app/frontend/.next ./frontend/.next
COPY --from=frontend-build /app/frontend/node_modules ./frontend/node_modules
COPY --from=frontend-build /app/frontend/package*.json ./frontend/
COPY --from=frontend-build /app/frontend/next.config.mjs ./frontend/
COPY --from=frontend-build /app/frontend/public ./frontend/public
COPY scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh

RUN mkdir -p /app/data \
  && chmod +x /app/scripts/docker-entrypoint.sh

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=20s \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null \
    && curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/health" >/dev/null \
    || exit 1

CMD ["/app/scripts/docker-entrypoint.sh"]
