# Variables
IMAGE_NAME = vm75/llama-web-ui
PORT_UI = 5000

.PHONY: all build run test stop clean logs

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
	@echo "Services started! Access the UI at http://localhost:$(PORT_UI)"

# Test if the Web UI is responding
test:
	@echo "Checking container status..."
	podman compose ps
	@echo "Pinging the Web UI..."
	@sleep 2
	curl -s -f http://localhost:$(PORT_UI) > /dev/null && \
		echo "✅ Test Passed: Web UI is accessible on port $(PORT_UI)!" || \
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
