FROM python:3.12-slim

WORKDIR /app

# poppler-utils is required by pdf2image to render PDF pages to images
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Shell form (not exec/array form) is required here so that $PORT
# actually gets expanded by the shell at container start.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}