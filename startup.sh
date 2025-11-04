#!/bin/bash

# Production startup script
export FLASK_ENV=production
export SECRET_KEY=${SECRET_KEY:-"your-production-secret-key"}

# Create logs directory
mkdir -p logs

# Start with Gunicorn
echo "Starting Flask API with Gunicorn..."
gunicorn --config gunicorn.conf.py wsgi:application