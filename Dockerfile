FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

WORKDIR /app

# Install Xvfb for virtual display (headless=False Chromium requires it)
RUN apt-get update && apt-get install -y --no-install-recommends xvfb && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Install Chromium browser binaries
RUN uv run playwright install chromium

# Copy source
COPY src/ src/
COPY resume.txt .
COPY config.yaml .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Persist jobs.db here
VOLUME ["/app/data"]

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
