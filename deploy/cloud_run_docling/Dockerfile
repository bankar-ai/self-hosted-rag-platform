FROM python:3.12-slim

WORKDIR /app

# opencv-python (pulled in transitively by docling's rapidocr OCR backend) needs these X11/GL
# shared libraries at import time -- python:3.12-slim's minimal base doesn't include them.
# Found live: the build failed with "ImportError: libxcb.so.1: cannot open shared object file"
# the first time this image tried to actually import cv2.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download docling's model weights at build time -- baked into the image layer, not
# fetched from HuggingFace at request time (which hit HF's rate limit in practice on cold
# start, causing the first real request to fail outright rather than just being slow).
RUN docling-tools models download

COPY main.py .

ENV PORT=8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
