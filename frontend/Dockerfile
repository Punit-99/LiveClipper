# syntax=docker/dockerfile:1.7
# LiveClipper Frontend Dockerfile — fully optimized
# Multi-stage: cached npm install, production build, nginx-alpine runtime as non-root.

ARG NODE_VERSION=20
ARG NGINX_VERSION=stable-alpine

# =============================================================
# Stage 1: DEPS — install node_modules with a cache mount
# =============================================================
FROM node:${NODE_VERSION}-alpine AS deps

WORKDIR /app

# Lockfile layer only — maximizes cache reuse across source edits.
COPY package.json package-lock.json ./

RUN --mount=type=cache,target=/root/.npm \
    npm ci --legacy-peer-deps --prefer-offline --no-audit --fund=false


# =============================================================
# Stage 2: BUILDER — produce the static bundle
# =============================================================
FROM node:${NODE_VERSION}-alpine AS builder

ENV NODE_ENV=production \
    CI=true

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build


# =============================================================
# Stage 3: RUNTIME — nginx serving the SPA, non-root
# =============================================================
FROM nginx:${NGINX_VERSION} AS runner

# curl is used only for the healthcheck (~250 KB on alpine — acceptable).
RUN apk add --no-cache curl tini

# Nginx config and built assets.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

# Run nginx as the non-root 'nginx' user that the base image already ships.
# Rewrite the main config to drop the privileged master, bind PID to a writable
# path, and point temp/log dirs at locations our user can write to.
RUN set -eux; \
    sed -i \
        -e '/^user /d' \
        -e 's|pid\s\+/var/run/nginx.pid;|pid /tmp/nginx.pid;|' \
        /etc/nginx/nginx.conf; \
    mkdir -p /var/cache/nginx /var/run /tmp/nginx; \
    chown -R nginx:nginx \
        /var/cache/nginx /var/log/nginx /etc/nginx/conf.d \
        /usr/share/nginx/html /tmp/nginx; \
    # Ensure listening on 3000 doesn't need CAP_NET_BIND_SERVICE (>1024, so fine).
    true

USER nginx

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -fsS http://127.0.0.1:3000/ >/dev/null || exit 1

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["nginx", "-g", "daemon off;"]
