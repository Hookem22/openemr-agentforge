FROM python:3.12-slim
WORKDIR /app
# Paths are relative to the repo root, not this directory: Railway's git-connected build for this
# service uses the repo root as its build context (RAILWAY_DOCKERFILE_PATH points at this file, but
# there is no separate "root directory" override wired up), so COPY sources must be repo-root-relative.
COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent/app ./app
# Week 2's guideline corpus (agent/data/guidelines/*.md) -- found missing here via a live production
# /ready check reporting vector_index as "zero chunks" right after this Dockerfile's first Week 2
# deploy: it only ever copied agent/app, so rag.py's load_corpus() correctly found nothing at
# runtime. The gitignored embeddings cache (guidelines_index_cache.json) intentionally isn't copied
# -- rag.py rebuilds it against Voyage automatically on first request if missing.
COPY agent/data ./data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
