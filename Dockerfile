FROM debian:bookworm-slim

# Install dependencies
RUN apt-get update && apt-get install -y \
  build-essential \
  cmake \
  git \
  python3 \
  python3-pip \
  python3-venv \
  procps \
  && rm -rf /var/lib/apt/lists/*

# Create a non-root user for rootless podman compatibility
RUN useradd -m -s /bin/bash llama
USER llama
WORKDIR /home/llama/app

# Set up virtual environment and install Python requirements
COPY --chown=llama:llama server/requirements.txt .
RUN python3 -m venv venv && \
  ./venv/bin/pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY --chown=llama:llama server/ server/
# Ship config.json as the default seed; the live config lives in data/config.json
COPY --chown=llama:llama config.json config.default.json
COPY --chown=llama:llama VERSION .

# Pre-create the data directory so the volume mount lands cleanly
# (models/ and the live config.json are created at runtime by ensure_data_dir)
RUN mkdir -p data

# Expose Web UI port and Llama.cpp port
EXPOSE 5000 8080

# Start the web server
CMD ["./venv/bin/python", "server/server.py"]