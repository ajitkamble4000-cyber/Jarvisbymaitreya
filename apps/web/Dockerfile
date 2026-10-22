# syntax=docker/dockerfile:1
FROM node:22-alpine AS build
WORKDIR /app

# Optional extra trust anchors (see extra-ca/README.md). npm reads NODE_EXTRA_CA_CERTS.
COPY extra-ca/ /usr/local/share/extra-ca/
RUN cat /usr/local/share/extra-ca/*.crt >> /etc/ssl/certs/ca-certificates.crt 2>/dev/null || true
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
