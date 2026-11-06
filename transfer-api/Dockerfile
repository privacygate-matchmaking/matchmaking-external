FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt pyproject.toml ./
COPY services ./services
RUN pip install --no-cache-dir .
COPY --from=privacygate pyproject.toml /opt/privacygate/
COPY --from=privacygate src /opt/privacygate/src
RUN pip install --no-cache-dir "/opt/privacygate[web]" \
 && useradd --system --uid 10001 app \
 && mkdir -p /data/transfer-staging /data/transfer-verified /var/log/matchmaking \
 && chown -R app /data/transfer-staging /data/transfer-verified /var/log/matchmaking
USER app
EXPOSE 8000
CMD ["uvicorn","ieum.transfer:create_app","--factory","--host","0.0.0.0","--port","8000","--no-access-log"]
