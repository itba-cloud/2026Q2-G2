# The workers' image, which Fargate pulls from ECR. Unlike TP1/TP2, the code
# ships inside the image: a task in a VPC without internet has no other way
# to get it.
#
# Built by `make worker-image` with app/ as the context. What the context
# sends is whitelisted in Dockerfile.dockerignore, next to this file.
FROM python:3.12-slim

COPY shared/requirements.txt /tmp/requirements/shared/requirements.txt
COPY batch/worker/requirements.txt /tmp/requirements/batch/worker/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements/batch/worker/requirements.txt \
 && rm -rf /tmp/requirements

# Flat, like the Lambda packages: the worker's modules next to app/shared's,
# so `from common import ...` resolves from the script's own directory.
WORKDIR /app
COPY shared/*.py batch/worker/*.py ./

ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]
