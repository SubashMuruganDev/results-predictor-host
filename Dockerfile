FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir . && useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 7860
CMD ["sh", "-c", "exec python3 -m results_predictor serve --host 0.0.0.0 --port ${PORT:-7860}"]
