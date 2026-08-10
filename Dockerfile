FROM docker:cli AS docker-cli

FROM python:3.12-alpine

WORKDIR /app

# Copy docker CLI and compose plugin statically to bypass Fedora DNS issues during build
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Ensure __init__.py exists to prevent ModuleNotFoundError in case it was stripped during transfer
RUN touch src/core/__init__.py

# Run the app
ENTRYPOINT ["python", "src/__main__.py"]
