# Variables
IMAGE_NAME = vm75/llama-controller
LLAMA_CONTROLLER_PORT ?= 5000

.PHONY: all build run run-native test stop clean logs

# Default target
all: build run

# Build the Podman image
build:
	@echo "Building Podman image $(IMAGE_NAME)..."
	podman build -t $(IMAGE_NAME) .

# Run the container using podman compose
run:
	@echo "Starting services with podman compose..."
	podman compose up -d
	@echo "Services started! Access the UI at http://localhost:$(LLAMA_CONTROLLER_PORT)"

# Run the server natively without a container
run-native:
	@echo "Running server natively..."
	@if [ ! -d "venv" ]; then \
		echo "Creating virtual environment..."; \
		python3 -m venv venv; \
	fi
	@echo "Installing dependencies..."
	@./venv/bin/pip install -q -r server/requirements.txt
	@echo "Starting Flask server natively..."
	@NATIVE_RUN=1 ./venv/bin/python server/server.py

# Test if the Web UI is responding
test:
	@echo "Checking container status..."
	podman compose ps
	@echo "Pinging the Web UI..."
	@sleep 2
	curl -s -f http://localhost:$(LLAMA_CONTROLLER_PORT) > /dev/null && \
		echo "✅ Test Passed: Web UI is accessible on port $(LLAMA_CONTROLLER_PORT)!" || \
		echo "❌ Test Failed: Web UI did not respond."

# Stop the container
stop:
	@echo "Stopping services..."
	podman compose stop

# Clean up the container (removes container, keeps volumes)
clean:
	@echo "Removing containers..."
	podman compose down

# Tail the container logs
logs:
	podman compose logs -f

sh:
	podman exec -ti llama-server sh
