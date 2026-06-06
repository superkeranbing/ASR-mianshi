#!/bin/sh
set -e

mc alias set local http://localhost:9000 minioadmin minioadmin

# Create bucket if not exists
mc mb local/tts-mianshi --ignore-existing

# Set bucket policy to allow downloads (for audio playback in browser)
mc anonymous set download local/tts-mianshi

echo "MinIO bucket 'tts-mianshi' initialized"
