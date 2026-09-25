FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    espeak-ng \
    fonts-dejavu-core \
    fonts-liberation \
    fonts-noto-core \
    fonts-noto-ui-core \
    fonts-noto-unhinted \
    fontconfig \
    curl \
    nodejs \
    ca-certificates \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY --chown=appuser:appuser . .

RUN mkdir -p /app/data /tmp/youtube_recap && chown -R appuser:appuser /app /tmp/youtube_recap

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import pathlib; pathlib.Path('/app/data').exists()" || exit 1

CMD ["python", "-m", "bot.main"]
