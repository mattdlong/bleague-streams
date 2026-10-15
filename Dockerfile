FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Playwright Chromium not needed — B.LEAGUE source has no browser dependency
# (unlike nbl1-streams which requires it for SBS auth)
COPY main.py .
COPY sources/ sources/
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
