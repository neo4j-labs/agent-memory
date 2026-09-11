# Google Cloud Financial Advisor - Frontend Dockerfile
# Multi-stage build for production

# Build stage
FROM node:22-alpine AS builder

WORKDIR /app

# Copy manifests first so dependency installs stay cached
COPY package*.json ./

RUN npm ci

# Copy source code
COPY . .

# VITE_* values are inlined at build time. Leave empty to call the backend
# through this image's nginx proxy (see nginx.conf.template); set it to an
# absolute URL to point the SPA at a separately deployed backend.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

# Production stage
FROM nginx:alpine

# Copy built assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx:alpine renders /etc/nginx/templates/*.conf.template with envsubst at
# container start-up, so BACKEND_URL (and PORT, for Cloud Run) are runtime
# configuration rather than baked into the image.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

ENV BACKEND_URL=http://backend:8080
ENV BACKEND_HOST=backend
ENV PORT=80

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
