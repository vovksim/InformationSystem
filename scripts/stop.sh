#!/bin/bash
set -e

echo "=== Stopping Information System ==="

# Move to compose directory
cd "$(dirname "$0")/../compose" || exit
echo "Now in directory: $(pwd)"

# Stop and remove containers, networks, volumes defined in compose
docker compose down

echo "=== Containers stopped successfully ==="

