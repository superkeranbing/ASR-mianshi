FROM python:3.12-slim
WORKDIR /app

# System deps: ffmpeg for audio, libgomp1 for torch/pytorch
RUN apt-get update && apt-get install -y --no-install-recommends     ffmpeg libsndfile1 libgomp1     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install PyTorch first from official index (tsinghua mirror often fails for torch)
RUN pip install --no-cache-dir torch torchaudio torchcodec --index-url https://download.pytorch.org/whl/cpu

# Install the rest from tsinghua mirror
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Extra deps not in requirements.txt
RUN pip install --no-cache-dir psycopg2-binary pydub soundfile librosa -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
