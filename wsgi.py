#!/usr/bin/env python3
"""
WSGI Entry Point for Production Deployment
"""
import os
from app import app

# Set production environment
os.environ.setdefault('FLASK_ENV', 'production')

# Create application instance
application = app

if __name__ == "__main__":
    # This won't be called in production
    app.run()