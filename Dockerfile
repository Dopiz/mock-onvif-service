# Multi-stage build for Mock ONVIF Camera Service
FROM python:3.13-slim AS base

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    wget \
    lsof \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p data/videos data/cameras data/snapshots logs/onvif logs/ffmpeg static

# Expose ports
# 9999: Web UI
# 12000-12999: ONVIF services (support up to 1000 cameras)
EXPOSE 9999
EXPOSE 12000-12999

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9999/cameras || exit 1

# Run the application
CMD ["python3", "run.py"]

