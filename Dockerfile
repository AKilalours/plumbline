# Plumbline in one image: the pipeline, the agent's Slack endpoint, Dagster and the lake publisher.
# Data lives in a mounted volume (/app/data), never in the image; secrets come from the environment.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DAGSTER_HOME=/app/data/dagster
WORKDIR /app

RUN useradd --create-home --uid 10001 plumbline

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml README.md ./
COPY src ./src
# Editable: the package finds dbt/ and sources/ next to src/, as in a checkout.
RUN pip install -e ".[orchestration,lake]"

COPY sources ./sources
COPY dbt ./dbt
COPY docs ./docs
COPY skills ./skills
COPY evals ./evals
COPY scripts ./scripts
COPY tests ./tests
COPY ops/roles.example.yaml ./ops/roles.example.yaml

RUN mkdir -p /app/data && chown -R plumbline /app
USER plumbline
VOLUME ["/app/data"]
EXPOSE 3000 3001

CMD ["python", "-m", "plumbline", "--score"]
