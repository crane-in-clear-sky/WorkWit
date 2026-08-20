FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt
COPY . /app
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
