# Tiny image: python stdlib only (+ optional anthropic SDK). Runs as non-root, read-only friendly.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY jarvis ./jarvis
ARG EXTRAS=""
RUN pip install --no-cache-dir ".${EXTRAS}" && useradd --create-home --uid 10001 jarvis
USER jarvis
# Inside the container bind all interfaces; docker-compose publishes the port on host 127.0.0.1 only.
ENV JARVIS_DASHBOARD_HOST=0.0.0.0
EXPOSE 8777
ENTRYPOINT ["jarvis"]
CMD ["demo", "--no-browser"]
