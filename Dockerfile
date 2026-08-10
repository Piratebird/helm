FROM python:3.12-alpine

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Run the app
ENTRYPOINT ["python", "src/__main__.py"]
