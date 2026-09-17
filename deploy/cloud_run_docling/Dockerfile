FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download docling's model weights at build time -- baked into the image layer, not
# fetched from HuggingFace at request time (which hit HF's rate limit in practice on cold
# start, causing the first real request to fail outright rather than just being slow).
RUN docling-tools models download

COPY main.py .

ENV PORT=8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
