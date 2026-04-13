#!/bin/sh
set -e

echo "waiting for minio to be ready...."
until /usr/bin/mc alias set local http://minio:9000 minioadmin minioadmin; do
  sleep 2
done

echo "Creating FedSentinel buckets..."
/usr/bin/mc mb -p local/fedsentinel-warehouse || true
/usr/bin/mc mb -p local/fedsentinel-raw || true
/usr/bin/mc mb -p local/fedsentinel-curated || true
/usr/bin/mc mb -p local/fedsentinel-quarantine || true

echo "FedSentinel MinIO bucket setup complete."