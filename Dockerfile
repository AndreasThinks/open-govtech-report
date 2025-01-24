FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install UV
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY . .

# Install dependencies using UV
RUN uv pip install -e . && \
    uv pip install datasette datasette-dashboards datasette-vega

# Create data directory if it doesn't exist
RUN mkdir -p data

# Expose port for Datasette
EXPOSE 8001

# Copy start script
COPY start.sh .
RUN chmod +x start.sh

# Run both the scraper and Datasette
CMD ["./start.sh"]
