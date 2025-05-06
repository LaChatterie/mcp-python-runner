# Use a Python image with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Set up the app directory
WORKDIR /app

# Create a shared project directory with appropriate permissions
RUN mkdir -p /project && chmod 777 /project

# Set environment variables for uv and the project directory
ENV UV_CACHE_DIR=/project/.cache/uv
ENV PYTHON_PROJECT_DIR="/project"
ENV UV_LINK_MODE=copy
ENV PYTHONPATH ="/project/.venv/bin:$PYTHONPATH"

# Copy the project files
COPY . /app

RUN echo '#!/bin/bash\n\
set -x\n\
# Initialize project structure\n\
mkdir -p /project/.cache/uv\n\
\n\
# Create and set up the project virtual environment if it doesn't exist\n\
if [ ! -d "/project/.venv" ]; then\n\
    echo "Creating new virtual environment in /project/.venv"\n\
    uv venv /project/.venv\n\
    \n\
    # Install the application into the project environment\n\
    echo "Installing application into project environment..."\n\
    uv pip install /app\n\
fi\n\
\n\
# Execute the original entrypoint with the project environment\n\
exec mcp-python-runner --dir /project' > /app/entrypoint-wrapper.sh && chmod +x /app/entrypoint-wrapper.sh

# when running the container, use our wrapper script
ENTRYPOINT ["/app/entrypoint-wrapper.sh"]

# Declare the volume for Docker to recognize it as a mount point
VOLUME ["/project"]

