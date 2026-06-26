#!/bin/bash
set -e

echo "=== Frame Atlas Startup ==="

# Install backend dependencies
echo "Installing Python dependencies..."
cd /app/backend
pip install -r requirements.txt

# Install frontend dependencies
echo "Installing Node dependencies..."
cd /app/frontend
npm install

# Build frontend
echo "Building frontend..."
npm run build

# Copy built frontend to backend static folder
echo "Setting up frontend serving..."
mkdir -p /app/backend/static
cp -r /app/frontend/dist/* /app/backend/static/

# Start backend (which will serve the frontend)
echo "Starting Flask backend..."
cd /app/backend
python app.py
