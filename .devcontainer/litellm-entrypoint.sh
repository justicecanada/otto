#!/bin/sh
# Entrypoint script for LiteLLM container
# Generates litellm-config.yaml from template using environment variables, then starts LiteLLM

set -e

# Source the .env file directly at runtime to pick up any changes
# This ensures we always have the latest values, even after dev_setup.sh runs
# or when switching branches with different credentials
ENV_FILE="/app/.env"
ENV_EXAMPLE="/app/.env.example"

# Load .env.example first (defaults), then .env (overrides) if it exists
if [ -f "$ENV_EXAMPLE" ]; then
	echo "LiteLLM: Loading defaults from .env.example..."
	set -a
	# shellcheck source=/dev/null
	. "$ENV_EXAMPLE"
	set +a
fi

if [ -f "$ENV_FILE" ]; then
	echo "LiteLLM: Loading credentials from .env..."
	set -a
	# shellcheck source=/dev/null
	. "$ENV_FILE"
	set +a
fi

# Check if required credentials are available
if [ -z "$AZURE_OPENAI_KEY" ] || [ "$AZURE_OPENAI_KEY" = "your-key-here" ]; then
	echo "=========================================="
	echo "LiteLLM: AZURE_OPENAI_KEY not configured."
	echo ""
	echo "This is expected on FIRST RUN before running dev_setup.sh."
	echo "After running 'bash dev_setup.sh' inside the app container,"
	echo "rebuild the devcontainer to start LiteLLM with real credentials."
	echo ""
	echo "Waiting indefinitely to keep container running..."
	echo "=========================================="
	# Sleep forever so the container stays up and doesn't cause restart loops
	exec sleep infinity
fi

# Generate config from template using sed (envsubst not available in this image)
echo "LiteLLM: Generating config from template..."
sed -e "s|\${OPENAI_SERVICE_NAME}|${OPENAI_SERVICE_NAME}|g" \
	-e "s|\${CHUNK_EMBEDDING_PARALLEL_REQUESTS}|${CHUNK_EMBEDDING_PARALLEL_REQUESTS:-10}|g" \
	/app/litellm-config-template.yaml >/app/litellm-config.yaml

# Start LiteLLM with the generated config
echo "LiteLLM: Starting with generated config..."
exec litellm --config /app/litellm-config.yaml "$@"
