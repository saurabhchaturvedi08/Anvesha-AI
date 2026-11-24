# The Anvesha web app. Build from the repository root (it uses the pnpm workspace):
#   docker build -f apps/web/Dockerfile .
# Pinned to a Node release at least a week old (same supply-chain rule as dependencies).
FROM node:24.22.3-alpine AS base
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm install --global pnpm@12.23.0
WORKDIR /repo

FROM base AS deps
# Dependencies first, so code changes don't invalidate this layer.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/api-client/package.json packages/api-client/
RUN pnpm install --frozen-lockfile --filter "@anvesha/web..."

FROM deps AS build
COPY packages/api-client packages/api-client
COPY apps/web apps/web
RUN pnpm --filter @anvesha/web build

FROM node:24.22.3-alpine AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup -S anvesha && adduser -S -G anvesha anvesha
# The standalone server mirrors the repository layout (the workspace root is traced).
COPY --from=build --chown=anvesha:anvesha /repo/apps/web/.next/standalone ./
COPY --from=build --chown=anvesha:anvesha /repo/apps/web/.next/static ./apps/web/.next/static
USER anvesha
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
