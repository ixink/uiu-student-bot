# Use official Python slim image for smaller size
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy project files
COPY . .

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome
RUN wget -q -O /tmp/google-chrome-key.asc https://dl-ssl.google.com/linux/linux_signing_key.pub \
    && mkdir -p /etc/apt/keyrings \
    && mv /tmp/google-chrome-key.asc /etc/apt/keyrings/google-chrome.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/google-chrome.asc] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV BOT_TOKEN=$BOT_TOKEN
ENV WEBHOOK_URL=https://uiu-student-bot.onrender.com/webhook
ENV PORT=8443

# Command to run the bot
CMD ["python", "app.py"]
