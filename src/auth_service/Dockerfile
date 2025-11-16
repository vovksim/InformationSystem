# Use official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy only requirements first for better caching
COPY requirements.txt .

# Upgrade pip and install dependencies
RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application
COPY . .

# Expose application and debug ports
EXPOSE 5000

# Start the app with debugpy so it can be debugged from VS Code
CMD ["python", "app.py"]
