FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Shell form (not exec/array form) is required here so that $PORT
# actually gets expanded by the shell at container start. Railway
# injects PORT as an environment variable; exec-form CMD would pass
# the literal string "$PORT" straight to uvicorn instead of the value.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}