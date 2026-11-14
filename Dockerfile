# ================================================================
# Z-PACT Enterprise Ecosystem — Unified Root Dockerfile (Railway)
# يشغّل المنصة الأساسية FastAPI + محرك SMS Webhook + بوت التيليجرام + الميني آب معاً
# بدون الحاجة لأي متغيرات بيئية يدوية على Railway
# ================================================================

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1 \
    ZPACT_LOW_CPU_MODE=1 \
    PORT=8000

WORKDIR /app

# مكتبات النظام اللازمة لـ PyMuPDF و OpenCV و Pillow والخطوط العربية و PostgreSQL/Healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libpq5 \
    curl \
    ca-certificates \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# تثبيت حزم بايثون الموحدة للمنظومة بالكامل
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# نسخ كامل ملفات المشروع وقاعدة البيانات والملازم والأصول
COPY . /app

# تجهيز مجلدات التخزين المطلوبة للتشغيل السحابي المباشر
RUN mkdir -p \
    /app/Z-PACT-main/storage/originals \
    /app/Z-PACT-main/storage/stamped \
    /app/Z-PACT-main/storage/previews \
    /app/Z-PACT-main/storage/covers \
    /app/Z-PACT-main/storage/frames \
    /app/Z-PACT-main/storage/logos \
    /app/Z-PACT-main/storage/materials \
    /app/Z-PACT-main/storage/receipts \
    /app/Z-PACT-main/storage/watermarks

EXPOSE 8000

CMD ["python", "-u", "orchestrator.py", "--railway"]
