#!/bin/bash
set -e

echo "Creating NATS JetStream stream for FedSentinel..."

docker run --rm --network fedsentinel_fedsentinel_net natsio/nats-box:latest /bin/sh -c "
nats --server nats://fedsentinel-nats:4222 stream add FEDSENTINEL_EDGE_INSIGHTS \
  --subjects 'fedsentinel.edge.insights.*' \
  --storage file \
  --retention limits \
  --max-age 24h \
  --defaults
"

echo "NATS stream FEDSENTINEL_EDGE_INSIGHTS created."