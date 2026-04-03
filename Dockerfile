FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for Pillow and other image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy both repositories' requirements and install them
# Note: In a Docker build context, we typically can't go "up" a directory.
# This assumes the build context is the parent directory of both repos.
COPY immich-autoindex/requirements.txt requirements.txt
COPY PhotoPainter-E-Ink-Spectra-6-image-converter/requirements.txt requirements_source.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements_source.txt

# Ensure the parent directory structure is what sync.py expects (../PhotoPainter...)
WORKDIR /app/immich-autoindex
COPY immich-autoindex/sync.py .
COPY PhotoPainter-E-Ink-Spectra-6-image-converter /app/PhotoPainter-E-Ink-Spectra-6-image-converter

CMD ["python", "sync.py"]
