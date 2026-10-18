# Imagen única para todas las Lambdas; cada función cambia el CMD (handler).
FROM public.ecr.aws/lambda/python:3.12

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

COPY pyproject.toml uv.lock ${LAMBDA_TASK_ROOT}/
RUN cd ${LAMBDA_TASK_ROOT} && \
    uv export --frozen --no-dev --no-emit-project --no-hashes -o requirements.txt && \
    uv pip install --system --target ${LAMBDA_TASK_ROOT} -r requirements.txt && \
    rm requirements.txt

COPY src/coach ${LAMBDA_TASK_ROOT}/coach

ENV MPLCONFIGDIR=/tmp/matplotlib

CMD ["coach.handlers.webhook.handler"]
