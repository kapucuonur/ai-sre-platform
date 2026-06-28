# Stage 1: Build React App
FROM node:18-alpine AS builder
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
ARG VITE_API_URL=https://sre-api.trihonor.com
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html

# Inline Nginx configuration to support SPA routing under /sre and API proxying
RUN echo 'server { \
    listen 80; \
    location /sre { \
        alias /usr/share/nginx/html; \
        index index.html index.htm; \
        try_files $uri $uri/ /sre/index.html; \
    } \
    location /api { \
        proxy_pass http://backend:8000; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
    } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
