# Stage 1: Build React static assets
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package manifests and install dependencies
COPY package*.json ./
RUN npm install --legacy-peer-deps

# Copy source code and build production bundle
COPY . .
RUN npm run build

# Stage 2: Serve with lightweight Nginx alpine image
FROM nginx:alpine AS runner

# Copy built frontend assets
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy custom Nginx configuration with API & WebSocket reverse proxies
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 3000
EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
