# Stage 1: Build React frontend with Node.js
FROM node:18-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend with built frontend
FROM python:3.11-slim
WORKDIR /app

# Install Python dependencies
COPY backend/requirements_v2.txt ./
RUN pip install --no-cache-dir -r requirements_v2.txt

# Copy backend code
COPY backend/ ./

# Copy built frontend from stage 1
COPY --from=frontend-build /app/frontend/dist ./static/

# Expose port (Railway sets $PORT environment variable)
EXPOSE ${PORT:-5000}

# Start the Flask app
CMD ["python", "app_v2.py"]
