FROM python:3.12-slim

WORKDIR /app

# poppler-utils is required by pdf2image to render PDF pages to images.
# libgl1 provides libGL.so.1, which cadquery's OCP bindings link against.
# The production review route imports cadquery at startup, so without it
# the app fails to import and the container never binds a port.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# The artifact working directory. `require_artifact_working_directory` refuses a
# directory that does not exist and creates nothing itself, so the directory a
# fabrication drawing is built in must already be in the image. It lives under /tmp
# because it is scratch space only: the durable artifact is uploaded to the
# `fabrication-drawings` bucket before the resolution is recorded, and nothing reads
# this path again afterwards, so it must not and does not survive a deployment.
# `connection_workspace` creates each project/connection directory beneath it.
# The path must also be set as ARTIFACT_WORKING_DIR; there is deliberately no default.
RUN mkdir -p /tmp/steelspec-artifacts

EXPOSE 8000

# Shell form (not exec/array form) is required here so that $PORT
# actually gets expanded by the shell at container start.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
