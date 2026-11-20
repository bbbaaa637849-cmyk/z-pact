# ================================================================
# Z-PACT Enterprise Ecosystem — Unified Root Dockerfile (Hugging Face Spaces & Railway)
# يشغّل المنصة الأساسية FastAPI + محرك SMS Webhook + بوت التيليجرام + الميني آب معاً
# جاهز للعمل المباشر على Hugging Face Spaces (المنفذ 7860 ومستخدم غير جذري UID 1000) و Railway
# ================================================================

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1 \
    ZPACT_LOW_CPU_MODE=1 \
    PORT=7860 \
    HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# إنشاء مستخدم غير جذري (UID 1000) متوافق مع متطلبات أمان Hugging Face Spaces
RUN useradd -m -u 1000 user

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

# نسخ كامل ملفات المشروع وقاعدة البيانات والملازم والأصول مع ملكية المستخدم 1000
COPY --chown=user:user . /app

# تجهيز مجلدات التخزين المطلوبة للتشغيل السحابي المباشر وضبط صلاحيات الكتابة للمستخدم 1000
RUN mkdir -p \
    /app/Z-PACT-main/storage/originals \
    /app/Z-PACT-main/storage/stamped \
    /app/Z-PACT-main/storage/previews \
    /app/Z-PACT-main/storage/covers \
    /app/Z-PACT-main/storage/frames \
    /app/Z-PACT-main/storage/logos \
    /app/Z-PACT-main/storage/materials \
    /app/Z-PACT-main/storage/receipts \
    /app/Z-PACT-main/storage/watermarks \
    /app/Z-PACT-main/storage/bot_tmp \
    && chown -R user:user /app \
    && chmod -R u+rwX,g+rwX /app/Z-PACT-main/storage

USER user

EXPOSE 7860

CMD ["python", "-u", "orchestrator.py", "--cloud"]
