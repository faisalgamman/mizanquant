FROM python:3.11-slim

WORKDIR /app

# Step 1: Copy only openbb_forecast package (including all subdirs like data/)
COPY openbb_forecast/openbb_forecast/ /app/openbb_forecast/openbb_forecast/

# Step 2: Copy the rest of the project (excluding openbb_forecast to avoid shadowing)
COPY app/ /app/app/
COPY requirements.txt railway.json /app/

# Debug: print openbb_forecast contents to stderr (visible in Railway build logs)
RUN python -c "import os; import sys; p='/app/openbb_forecast/openbb_forecast'; print('OPENBB_FORECAST CONTENTS:', file=sys.stderr); [print(f'  {e}', file=sys.stderr) for e in sorted(os.listdir(p))]; d=os.path.join(p,'data'); print('DATA DIR:', os.path.isdir(d), file=sys.stderr)"

# Add PYTHONPATH so openbb_forecast is importable
ENV PYTHONPATH="/app/openbb_forecast:$PYTHONPATH"

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:${PORT:-6910}/api/system/status').raise_for_status()"

CMD python app/workspace_server.py