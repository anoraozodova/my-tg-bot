FROM python:3.10-slim

# Устанавливаем ffmpeg, который нужен yt-dlp для обработки видео/аудио
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем файлы проекта
COPY main.py config.py ./

# Устанавливаем библиотеки прямо здесь
RUN pip install --no-cache-dir pyTelegramBotAPI yt-dlp requests cryptography

CMD ["python", "main.py"]
