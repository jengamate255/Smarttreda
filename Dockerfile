# Build the MCP server + web dashboard image
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY pyproject.toml ./
COPY mt5_mcp/requirements.txt ./mt5_mcp/
COPY requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -e ".[dev]"

# Copy application code
COPY . .

# Expose ports
# 8050 - web dashboard
# 9000 - MCP HTTP transport (if using streamable-http)
EXPOSE 8050 9000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.path.insert(0, 'mt5_mcp'); from client import get_client; c = get_client(); exit(0 if c.is_connected() else 1)" || exit 1

# Default command runs the MCP server (stdio transport for MCP clients)
# Override with web dashboard: CMD ["python", "web_dashboard.py"]
CMD ["python", "mt5_mcp/server.py"]