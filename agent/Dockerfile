FROM python:3.12-slim
WORKDIR /app
# Paths are relative to the repo root, not this directory: Railway's git-connected build for this
# service uses the repo root as its build context (RAILWAY_DOCKERFILE_PATH points at this file, but
# there is no separate "root directory" override wired up), so COPY sources must be repo-root-relative.
COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent/app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
