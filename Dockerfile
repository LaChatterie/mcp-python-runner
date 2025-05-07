
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# Set up a directory for the pre-installed application's virtual environment
ENV APP_VENV_DIR=/opt/app-venv

# Copy the application source code
# Assuming your Dockerfile is in the root of your project, and your app is defined by (e.g.) a pyproject.toml here.
WORKDIR /app
COPY . /app

# Pre-install the application into its own virtual environment
# Using --no-cache to avoid populating a cache that needs cleanup in this stage for this specific install.
# This installs the application defined by /app (e.g., from its pyproject.toml)
RUN uv venv ${APP_VENV_DIR} && \
    uv pip install --no-cache --python ${APP_VENV_DIR}/bin/python /app

# Use the same base image as it includes Python and uv (which might be needed for runtime installs in /project)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Environment variables for the final image
ENV APP_VENV_DIR=/opt/app-venv
# Add the pre-built venv's bin directory to PATH so its executables (including python and any app scripts) are found
ENV PATH="${APP_VENV_DIR}/bin:${PATH}"

ENV UV_CACHE_DIR=/project/.cache/uv
ENV PYTHON_PROJECT_DIR="/project"
ENV UV_LINK_MODE=copy

# Copy the pre-built virtual environment from the builder stage
COPY --from=builder ${APP_VENV_DIR} ${APP_VENV_DIR}

# Create the /project directory and the uv cache directory within it.
# /project is intended to be a volume.
RUN mkdir -p /project && chmod 777 /project && \
    mkdir -p ${UV_CACHE_DIR} && chmod 777 ${UV_CACHE_DIR}

WORKDIR /project

# Entrypoint wrapper script
RUN echo '#!/bin/bash\n\
set -e\n\
# Ensure project-specific uv cache directory exists (it was created during build, but this is a safe check)\n\
mkdir -p "${UV_CACHE_DIR}"\n\
\n\
exec mcp-python-runner --dir /project\n\
' > /usr/local/bin/entrypoint-wrapper.sh && chmod +x /usr/local/bin/entrypoint-wrapper.sh

ENTRYPOINT ["/usr/local/bin/entrypoint-wrapper.sh"]

# Declare the volume for Docker to recognize it as a mount point
VOLUME ["/project"]