# Construction de l'interface, puis service statique par nginx
FROM node:22-alpine AS build
WORKDIR /web
# Lot Z1 : installation exacte du verrou (package-lock.json), refusée s'il ne correspond pas à package.json
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Empreinte du commit (docker build --build-arg MARS_VERSION=…), montrée par l'indicateur « Version » de la barre d'état
ARG MARS_VERSION=dev
ENV MARS_VERSION=$MARS_VERSION
RUN npm run build

FROM nginx:1.28-alpine
# Relie le paquet du registre au dépôt (nettoyage par GitHub Actions, .github/workflows/nettoyage_registre.yml)
LABEL org.opencontainers.image.source="https://github.com/Antoine2Pommereau/mars_c2"
ARG MARS_VERSION=dev
LABEL org.opencontainers.image.revision=$MARS_VERSION
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
