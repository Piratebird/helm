FROM python:3.12-alpine

WORKDIR /app

# Install docker CLI and compose plugin via Alpine to avoid Go CVEs in official binaries
RUN apk add --no-cache docker-cli docker-cli-compose

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Install the application as a Python package
RUN pip install --no-cache-dir .

# Run the app
ENTRYPOINT ["python", "-m", "helm.cli"]
ENV PYTHONPATH=/app/src
