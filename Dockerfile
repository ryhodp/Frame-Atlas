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

# V44 (Day 26): without this, Python block-buffers stdout whenever it isn't a
# terminal — which is always, in a container. Every print() in app.py
# ([tagging], [auth], [crypto], [drive], [migration] …) would sit in a buffer
# for an unbounded time and be LOST OUTRIGHT if the process restarts or
# crashes before it fills. Measured locally: after startup, not one print()
# reached the log file across a whole session's worth of activity.
#
# This is load-bearing for the whole Day 26 except:pass audit — logging what
# an error handler discards is worthless if the log line never arrives — and
# is very likely part of why the V27 crop failure stayed invisible for weeks.
ENV PYTHONUNBUFFERED=1

# Install Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ ./

# Copy built frontend from stage 1
COPY --from=frontend-build /app/frontend/dist ./static/

# Expose port (Railway sets $PORT environment variable)
EXPOSE ${PORT:-5000}

# Start the Flask app
CMD ["python", "app.py"]
