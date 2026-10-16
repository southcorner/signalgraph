FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV TZ=Asia/Kolkata
EXPOSE 8000
CMD ["python", "-m", "signalgraph", "--host", "0.0.0.0", "--port", "8000"]
