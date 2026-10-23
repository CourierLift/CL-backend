# Dedicated backup/recovery runtime. Existing web service stays native Python.
# No credentials are build arguments or image files.
FROM postgres:16.15-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/pilot-python \
    && /opt/pilot-python/bin/pip install --no-cache-dir -r /app/requirements.txt
COPY ops/pilot-backup/install_age.py /app/install_age.py
RUN curl --fail --location --retry 2 --output /tmp/age.tar.gz \
      https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-amd64.tar.gz \
    && /opt/pilot-python/bin/python /app/install_age.py --archive /tmp/age.tar.gz --destination /opt/pilot-age \
    && rm /tmp/age.tar.gz
COPY scripts /app/scripts
COPY ops /app/ops
COPY tests /app/tests
COPY backend /app/backend
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini
ENV PATH="/opt/pilot-python/bin:/opt/pilot-age:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN python -c "import sys; sys.path.insert(0,'/app'); from scripts.recovery_artifacts import backup_module; m=backup_module(); m.Age(); m.Postgres(); print('PG16 and pinned age runtime passed')"
USER postgres
ENTRYPOINT ["python", "ops/pilot-recovery/runtime_entry.py"]
CMD ["python", "ops/pilot-recovery/cron_entry.py", "backup"]
