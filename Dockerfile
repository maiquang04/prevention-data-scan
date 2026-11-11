# One small image for the two jobs that need Python: serving the site and its search
# page, and rebuilding the site's data from the workbook. The code is not copied in.
# docker-compose.yml mounts this folder instead, so an updated workbook only needs the
# export run again and the search restarted, never a rebuild of this image.
FROM python:3.13-slim

# openpyxl reads the workbook. Everything else is the standard library.
RUN pip install --no-cache-dir openpyxl==3.1.5

# The folder is mounted read-only for the search, so Python must not try to write
# its byte-code cache into it; and print() should reach `docker compose logs` at once.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /site
