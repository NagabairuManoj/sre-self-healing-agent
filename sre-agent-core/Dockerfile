FROM python:3.9-slim

# Install system dependencies (git for cloning, patch for applying diff repairs)
RUN apt-get update && apt-get install -y \
    git \
    patch \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend files
COPY . .

# Expose port and start FastAPI
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
