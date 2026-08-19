FROM public.ecr.aws/lambda/python:3.12

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY lambda_handler.py .

# Lambda entry point: lambda_handler.lambda_handler
CMD ["lambda_handler.lambda_handler"]
