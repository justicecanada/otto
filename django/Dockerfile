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

# Run the tiktoken caching script and capture output
RUN python /django/cache_tiktoken.py 2>&1 | tee /tiktoken_cache_output.log

# Add diagnostic information to a single file
RUN echo "Tiktoken cache output:" > /diagnostic_info.log && \
    cat /tiktoken_cache_output.log >> /diagnostic_info.log && \
    echo "Tiktoken cache contents:" >> /diagnostic_info.log && \
    ls -l $TIKTOKEN_CACHE_DIR >> /diagnostic_info.log && \
    echo "Tiktoken cache file sizes:" >> /diagnostic_info.log && \
    du -sh $TIKTOKEN_CACHE_DIR/* >> /diagnostic_info.log || echo "No files in cache" >> /diagnostic_info.log && \
    echo "Tiktoken cache directory size:" >> /diagnostic_info.log && \
    du -sh $TIKTOKEN_CACHE_DIR >> /diagnostic_info.log && \
    echo "Available disk space:" >> /diagnostic_info.log && \
    df -h >> /diagnostic_info.log

CMD ["daphne", "--bind", "0.0.0.0", "--port", "8000", "otto.asgi:application"]
