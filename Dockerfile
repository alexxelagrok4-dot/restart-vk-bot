FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vk_re_start_bot_cloud.py .

CMD ["python", "vk_re_start_bot_cloud.py"]
