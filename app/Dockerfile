# EcoLinks image. The same image runs locally (uvicorn on $PORT) and on AWS Lambda:
# the AWS Lambda Web Adapter extension forwards Lambda events to the web server.
FROM python:3.12-slim

COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

ENV PORT=8080 \
    AWS_LWA_PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/healthz \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY app/requirements.txt app/requirements.txt
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r app/requirements.txt
COPY app/ app/

RUN useradd --system --uid 10001 ecolinks
USER 10001

ARG GG_GIT_SHA=dev
ENV GG_GIT_SHA=${GG_GIT_SHA}

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
