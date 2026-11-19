# LiveClipper Frontend Dockerfile (Optimized)
# Already multi-stage — minor improvements applied

# Stage 1: Build React static assets
FROM node:20-alpine AS builder

WORKDIR /app

# Copy lockfile separately for better layer caching
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

# Copy source and build
COPY . .
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:stable-alpine AS runner

# Copy only the built output
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy custom Nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]