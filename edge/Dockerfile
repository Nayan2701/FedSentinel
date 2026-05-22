FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY edge_node.py .

ENV PYTHONUNBUFFERED=1
CMD ["python", "edge_node.py"]
