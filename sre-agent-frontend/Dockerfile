# Stage 1: Build the React application
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package configurations
COPY package*.json ./

# Install npm dependencies
RUN npm install

# Copy all files
COPY . .

# Build production assets (creates dist/ folder)
RUN npm run build

# Stage 2: Serve the assets using Nginx
FROM nginx:stable-alpine

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy custom Nginx configuration for SPA routing
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
