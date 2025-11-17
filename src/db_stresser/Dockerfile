FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy all files from db_stresser folder into container
COPY . /app

# Install dependencies from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Run the stresser script
CMD ["python", "app.py"]
