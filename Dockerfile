# Hoops Warehouse: the code in a container. The data stay outside, mounted at /app/data.
#
#   docker build -t hoops-warehouse .
#   docker run --rm -v "$PWD/data:/app/data" hoops-warehouse                       # list the tables
#   docker run --rm -v "$PWD/data:/app/data" hoops-warehouse python -m warehouse.sql --file queries/home_advantage.sql
#   docker run --rm -v "$PWD/data:/app/data" hoops-warehouse python -m warehouse.build euroleague --season 2025
#   docker run --rm hoops-warehouse python -m pytest -q                              # the tests
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY warehouse/ warehouse/
COPY queries/ queries/
COPY analysis/ analysis/
COPY tests/ tests/
COPY README.md LICENSE ./

VOLUME ["/app/data"]
CMD ["python", "-m", "warehouse.sql"]
