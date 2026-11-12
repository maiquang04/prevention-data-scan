# One small image for the two jobs that need Python: serving the site and its search
# page, and rebuilding the site's data from data/sources.csv. Both use only the
# standard library. The code is not copied in: docker-compose.yml mounts this folder
# instead, so an updated source list only needs the export run again and the search
# restarted, never a rebuild of this image.
FROM python:3.13-slim

# The folder is mounted read-only for the search, so Python must not try to write
# its byte-code cache into it; and print() should reach `docker compose logs` at once.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /site
