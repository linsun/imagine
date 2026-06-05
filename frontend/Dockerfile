FROM nginx:1.27-alpine

# Defaults; override via env in docker-compose / k8s.
ENV BACKEND_HOST=backend \
    BACKEND_PORT=8080

# nginx image auto-runs envsubst on *.template -> /etc/nginx/conf.d/
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY index.html /usr/share/nginx/html/index.html

EXPOSE 80
