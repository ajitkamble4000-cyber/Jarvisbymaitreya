# syntax=docker/dockerfile:1
FROM node:22-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
# A managed or corporate proxy CA may be supplied as a BuildKit secret. It is
# trusted only by npm during dependency installation and is not retained.
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -s /run/secrets/proxy_ca ]; then \
        NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca npm ci --strict-ssl=true --no-audit --no-fund; \
    else \
        npm ci --strict-ssl=true --no-audit --no-fund; \
    fi
COPY . .
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
