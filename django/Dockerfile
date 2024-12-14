FROM python:3.11-slim-bullseye

WORKDIR /django

# Set environment variable for tiktoken cache
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache

# Install system dependencies
RUN apt-get update && apt-get install -y postgresql-client poppler-utils procps

# Copy requirements files
COPY requirements*.txt /tmp/pip-tmp/

# Install Python dependencies
RUN pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt -r /tmp/pip-tmp/requirements_dev_celery.txt \
    && rm -rf /tmp/pip-tmp

# Copy application code
COPY . /django

# Make scripts executable
RUN chmod +x /django/*.sh

# Create tiktoken cache directory
RUN mkdir -p $TIKTOKEN_CACHE_DIR

# Cache the tiktoken encoding files for all relevant models
RUN python -c "\
    import tiktoken; \
    models = ['gpt-4o-mini', 'gpt-4o', 'gpt-4-1106-preview', 'gpt-35-turbo-0125']; \
    for model in models: \
    tiktoken.encoding_for_model(model)"

CMD ["daphne", "--bind", "0.0.0.0", "--port", "8000", "otto.asgi:application"]
