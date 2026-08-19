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

# Install the application as a Python package
RUN pip install --no-cache-dir .

# Run the app
ENTRYPOINT ["python", "-m", "helm.cli"]
ENV PYTHONPATH=/app/src
