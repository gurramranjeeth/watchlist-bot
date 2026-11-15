FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .
# Keep the database on a mounted volume so your watchlist survives restarts
ENV DB_PATH=/data/watchlist.db
RUN mkdir -p /data
CMD ["python", "bot.py"]
