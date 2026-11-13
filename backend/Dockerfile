FROM python:3.12-slim
WORKDIR /app
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN pip install --no-cache-dir .
ENV DATABASE_URL=sqlite:////data/gateway.db
ENV MOCK_MODE=true
RUN mkdir -p /data && useradd --create-home gateway && chown gateway /data
USER gateway
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
