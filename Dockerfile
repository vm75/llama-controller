FROM debian:bookworm-slim

# CUDA packages are shipped in Debian's non-free component.
RUN sed -i 's/Components: main/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources

# Install dependencies
RUN apt-get update && apt-get install -y \
  build-essential \
  cmake \
  git \
  python3 \
  python3-pip \
  python3-venv \
  procps \
  sudo \
  && rm -rf /var/lib/apt/lists/*

# Create a non-root user for rootless podman compatibility. Runtime package
# installation is limited to apt-get, which build presets invoke as needed.
RUN useradd -m -s /bin/bash llama && \
  echo "llama ALL=(root) NOPASSWD: /usr/bin/apt-get" > /etc/sudoers.d/llama
USER llama
WORKDIR /home/llama/app

# Set up virtual environment and install Python requirements
COPY --chown=llama:llama server/requirements.txt .
RUN python3 -m venv venv && \
  ./venv/bin/pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY --chown=llama:llama server/ server/
# Ship default seeds; live config and presets live under data/.
COPY --chown=llama:llama config.default.json .
COPY --chown=llama:llama models.ini.example .
COPY --chown=llama:llama VERSION .

# Pre-create the data directory so the volume mount lands cleanly
# (models/ and the live config.json are created at runtime by ensure_data_dir)
RUN mkdir -p data

# Expose Web UI port and Llama.cpp port
EXPOSE 5000 8080

# Start the web server
CMD ["./venv/bin/python", "server/server.py"]
