# =============================================================================
# PHP-FPM Automation Agent — Docker Image
# =============================================================================
# This container packages the deployment tool and runs it against the HOST server.
# It uses nsenter to execute all commands (apt, systemctl, useradd, etc.) directly
# in the host's namespace — so the host gets configured, NOT the container.
#
# Build:
#   docker build -t php-deployer .
#
# Run:
#   docker run --rm --privileged --pid=host --network=host \
#     -v /:/host \
#     -v ./services.yml:/app/services.yml \
#     php-deployer deploy
# =============================================================================

FROM python:3.11-slim

LABEL maintainer="PHP-FPM Automation Agent"
LABEL description="Containerized PHP deployment agent that configures the host server"
LABEL version="1.0.0"

# Install minimal dependencies (nsenter comes from util-linux)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        util-linux \
        && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire tool
COPY . .

# Make entrypoint executable
RUN chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]

# Default command: show help
CMD ["--help"]
