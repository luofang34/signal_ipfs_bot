FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY bot.py .
COPY manage.py .

CMD ["python", "bot.py"]