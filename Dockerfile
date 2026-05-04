FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Convert UTF-16 BOM → UTF-8 if needed (Windows-generated requirements.txt)
RUN python -c "
import sys
with open('requirements.txt', 'rb') as f:
    raw = f.read()
if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
    text = raw.decode('utf-16').encode('utf-8')
    with open('requirements.txt', 'wb') as out:
        out.write(text)
    print('Converted requirements.txt UTF-16 -> UTF-8')
" && pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--log-level", "info", \
     "--access-logfile", "-"]
