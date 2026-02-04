FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure data dictionary exists for SQLite
RUN mkdir -p /app/data

EXPOSE 5005

CMD ["python", "main.py"]
