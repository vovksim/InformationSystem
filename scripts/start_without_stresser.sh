#!/bin/bash
# Navigate to the compose folder and start Docker Compose with debugging
# Starts all services except db_stresser

echo "=== Starting Information System ==="
echo "Current directory: $(pwd)"
echo "Script directory: $(dirname "$0")"
echo "Moving to compose directory..."

cd "$(dirname "$0")/../compose" || {
  echo "❌ ERROR: Cannot find compose directory"
  exit 1
}

echo "Now in directory: $(pwd)"
echo "Checking if Prometheus config exists..."

if [ -f "../compose/configs/prometheus/prometheus.yml" ]; then
  echo "✅ Found Prometheus config at ../configs/prometheus/prometheus.yml"
else
  echo "❌ Prometheus config NOT FOUND at ../configs/prometheus/prometheus.yml"
  echo "Contents of ../configs/prometheus/:"
  ls -la ../compose/configs/prometheus/
fi

echo "Checking Docker Compose file..."
if [ -f "docker-compose.yml" ]; then
  echo "✅ docker-compose.yml found"
else
  echo "❌ docker-compose.yml not found! Make sure it's inside ./compose/"
  exit 1
fi

# Get list of services from docker-compose.yml except db_stresser
SERVICES=$(docker compose config --services | grep -v '^db_stresser$')

echo "Starting services (excluding db_stresser):"
echo "$SERVICES"

docker compose up -d $SERVICES

echo "✅ All specified services started successfully (db_stresser excluded)."

