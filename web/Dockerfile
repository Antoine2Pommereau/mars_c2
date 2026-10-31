# Construction de l'interface, puis service statique par nginx
FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS build
WORKDIR /web
# Lot Z1 : installation exacte du verrou (package-lock.json), refusée s'il ne correspond pas à package.json
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Empreinte du commit (docker build --build-arg MARS_VERSION=…), montrée par l'indicateur « Version » de la barre d'état
ARG MARS_VERSION=dev
ENV MARS_VERSION=$MARS_VERSION
RUN npm run build

FROM nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
# Relie le paquet du registre au dépôt (nettoyage par GitHub Actions, .github/workflows/nettoyage_registre.yml)
LABEL org.opencontainers.image.source="https://github.com/Antoine2Pommereau/mars_c2"
ARG MARS_VERSION=dev
LABEL org.opencontainers.image.revision=$MARS_VERSION
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
