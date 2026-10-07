# Construction de l'interface, puis service statique par nginx
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# Relie le paquet du registre au dépôt (nettoyage par GitHub Actions, .github/workflows/nettoyage_registre.yml)
LABEL org.opencontainers.image.source="https://github.com/Antoine2Pommereau/mars_c2"
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
