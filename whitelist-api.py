from flask import Flask, request, jsonify
import subprocess
import tempfile
import os
import shutil
import base64
import urllib.parse
import logging
from logging.handlers import RotatingFileHandler

app = Flask(__name__)

# Production Configuration
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-production-secret-key-change-this'
    DEBUG = os.environ.get('FLASK_ENV') != 'production'
    TESTING = False
    # Git repository configuration
    BITBUCKET_URL = "git-bitbucket.aws.fico.com:8443"
    PROJECT_KEY = "SRE-PLATFORM"
    REPO_SLUG = "fico-pto-tenant"

app.config.from_object(Config)

# Configure logging for production
if not app.debug and not app.testing:
    if not os.path.exists('logs'):
        os.mkdir('logs')
    
    file_handler = RotatingFileHandler('logs/flask_app.log', maxBytes=10240000, backupCount=10)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('Flask API startup')

def validate_input(entry, environment, tenant_name):
    """Validate input parameters"""
    errors = []
    
    # Validate entry
    if not entry or len(entry.strip()) == 0:
        errors.append("Entry cannot be empty")
    elif len(entry) > 500:
        errors.append("Entry too long (max 500 characters)")
    
    # Validate environment
    allowed_envs = ['ort', 'int', 'prod', 'dev', 'staging', 'test']
    if environment and environment.lower() not in allowed_envs:
        errors.append(f"Environment must be one of: {', '.join(allowed_envs)}")
    
    # Validate tenant name
    if tenant_name and not tenant_name.replace('-', '').replace('_', '').isalnum():
        errors.append("Tenant name can only contain letters, numbers, hyphens, and underscores")
    
    return errors

@app.route('/whitelist/add', methods=['POST'])
def add_whitelist_entry():
    try:
        # Get parameters from both query params and JSON body
        environment = request.args.get('environment')
        tenant_name = request.args.get('tenant')
        file_name = request.args.get('file', 'Whitelist.csv')
        
        # From JSON body (fallback if not in query params)
        data = request.get_json() or {}
        entry = data.get('entry')
        
        # Use query params if available, otherwise use JSON body
        if not environment:
            environment = data.get('environment')
        if not tenant_name:
            tenant_name = data.get('tenant')
        if file_name == 'Whitelist.csv':
            file_name = data.get('file', 'Whitelist.csv')
        
        # Entry can also come from query params
        if not entry:
            entry = request.args.get('entry')
        
        # Validate required fields
        if not entry:
            return jsonify({
                "error": "Entry field is required",
                "usage": "Include 'entry' in JSON body or as query parameter"
            }), 400
        if not environment:
            return jsonify({
                "error": "Environment field is required", 
                "usage": "Use ?environment=ort or include in JSON body"
            }), 400
        if not tenant_name:
            return jsonify({
                "error": "Tenant field is required",
                "usage": "Use ?tenant=FICO-PTO-TENANT or include in JSON body"
            }), 400
        
        # Validate inputs
        validation_errors = validate_input(entry, environment, tenant_name)
        if validation_errors:
            return jsonify({
                "error": "Validation failed",
                "details": validation_errors
            }), 400
        
        # Construct branch name and folder path
        branch_name = f"jenkins-store-{environment.lower()}"
        folder_name = tenant_name
        
        # Get credentials from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Basic '):
            return jsonify({"error": "Basic Authorization required"}), 401
        
        # Decode credentials
        try:
            encoded_creds = auth_header.split(' ')[1]
            creds = base64.b64decode(encoded_creds).decode('utf-8')
            username, password = creds.split(':', 1)
        except (IndexError, ValueError) as e:
            return jsonify({"error": "Invalid authorization header format"}), 401
        
        # URL encode credentials to handle special characters
        encoded_username = urllib.parse.quote(username, safe='')
        encoded_password = urllib.parse.quote(password, safe='')
        
        # Repository URL with URL-encoded credentials
        repo_url = f"https://{encoded_username}:{encoded_password}@{app.config['BITBUCKET_URL']}/scm/sre-platform/fico-pto-tenant.git"
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp(prefix='whitelist_')
        
        try:
            # Clone the constructed branch
            app.logger.info(f"Cloning branch '{branch_name}' for user '{username}'")
            
            clone_result = subprocess.run([
                "git", "clone", "-b", branch_name, "--depth", "1", repo_url, temp_dir
            ], capture_output=True, text=True, timeout=120)
            
            if clone_result.returncode != 0:
                app.logger.error(f"Git clone failed: {clone_result.stderr}")
                return jsonify({
                    "error": f"Failed to clone branch '{branch_name}'",
                    "suggestion": f"Make sure branch '{branch_name}' exists in the repository"
                }), 500
            
            # Change to repository directory
            os.chdir(temp_dir)
            
            # Create folder structure if it doesn't exist
            if not os.path.exists(folder_name):
                app.logger.info(f"Creating folder structure: {folder_name}")
                os.makedirs(folder_name, exist_ok=True)
            
            # Construct full file path
            file_path = file_name
            
            # Check if file exists, create if not
            if not os.path.exists(file_path):
                with open(file_path, 'w') as f:
                    f.write(f"# Whitelist for {tenant_name} in {environment}\n")
                    f.write("# Format: IP,Description,Status\n")
            
            # Add the new entry to the file
            app.logger.info(f"Adding entry '{entry}' to {file_path} for tenant {tenant_name}")
            
            with open(file_path, "a") as f:
                f.write(f"{entry}\n")
            
            # Configure git user
            subprocess.run(["git", "config", "user.name", username], check=True)
            subprocess.run(["git", "config", "user.email", f"{username}@fico.com"], check=True)
            
            # Add, commit, and push changes
            subprocess.run(["git", "add", "."], check=True)
            
            commit_message = f"Add {tenant_name} whitelist entry in {environment}: {entry}"
            
            commit_result = subprocess.run([
                "git", "commit", "-m", commit_message
            ], capture_output=True, text=True)
            
            if commit_result.returncode != 0:
                app.logger.error(f"Git commit failed: {commit_result.stderr}")
                return jsonify({
                    "error": f"Failed to commit: {commit_result.stderr}"
                }), 500
            
            push_result = subprocess.run([
                "git", "push", "origin", branch_name
            ], capture_output=True, text=True, timeout=120)
            
            if push_result.returncode != 0:
                app.logger.error(f"Git push failed: {push_result.stderr}")
                return jsonify({
                    "error": f"Failed to push to branch '{branch_name}': {push_result.stderr}"
                }), 500
            
            app.logger.info(f"Successfully added entry for {tenant_name} in {environment}")
            
            return jsonify({
                "status": "success",
                "message": f"Entry added successfully to {file_path} in {environment} environment",
                "entry": entry,
                "environment": environment,
                "branch": branch_name,
                "tenant": tenant_name,
                "folder": folder_name,
                "file": file_name,
                "file_path": file_path,
                "commit_message": commit_message
            }), 200
            
        finally:
            # Clean up temporary directory
            try:
                os.chdir("/")
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception as cleanup_error:
                app.logger.error(f"Cleanup failed: {cleanup_error}")
                
    except Exception as e:
        app.logger.error(f"Unexpected error in add_whitelist_entry: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/whitelist/view', methods=['GET', 'POST'])
def view_whitelist():
    """View whitelist contents - supports both GET with query params and POST with JSON"""
    try:
        # Support both GET with query params and POST with JSON
        if request.method == 'GET':
            environment = request.args.get('environment')
            tenant_name = request.args.get('tenant')
            file_name = request.args.get('file', 'Whitelist.csv')
        else:
            data = request.get_json() or {}
            environment = request.args.get('environment') or data.get('environment')
            tenant_name = request.args.get('tenant') or data.get('tenant')
            file_name = request.args.get('file') or data.get('file', 'Whitelist.csv')
        
        if not environment or not tenant_name:
            return jsonify({
                "error": "Environment and tenant are required",
                "usage": "Use ?environment=ort&tenant=FICO-PTO-TENANT or include in JSON body"
            }), 400
        
        # Construct paths
        branch_name = f"jenkins-store-{environment.lower()}"
        folder_name = f"tenants/{tenant_name}"
        file_path = f"{folder_name}/{file_name}"
        
        # Get credentials
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({"error": "Authorization required"}), 401
        
        try:
            encoded_creds = auth_header.split(' ')[1]
            creds = base64.b64decode(encoded_creds).decode('utf-8')
            username, password = creds.split(':', 1)
        except (IndexError, ValueError):
            return jsonify({"error": "Invalid authorization header"}), 401
        
        # Use Bitbucket REST API to get file content
        import requests
        
        api_url = f"https://{app.config['BITBUCKET_URL']}/rest/api/1.0/projects/{app.config['PROJECT_KEY']}/repos/{app.config['REPO_SLUG']}/raw/{file_path}"
        params = {"at": branch_name}
        
        response = requests.get(api_url, auth=(username, password), params=params, verify=False, timeout=30)
        
        if response.status_code == 200:
            lines = response.text.strip().split('\n') if response.text.strip() else []
            return jsonify({
                "status": "success",
                "content": response.text,
                "lines": lines,
                "line_count": len(lines),
                "environment": environment,
                "branch": branch_name,
                "tenant": tenant_name,
                "folder": folder_name,
                "file": file_name,
                "file_path": file_path
            }), 200
        else:
            return jsonify({
                "error": "File not found or access denied",
                "file_path": file_path,
                "branch": branch_name,
                "status_code": response.status_code
            }), response.status_code
            
    except Exception as e:
        app.logger.error(f"Error in view_whitelist: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "tenant-environment-api",
        "version": "1.0.0",
        "environment": os.environ.get('FLASK_ENV', 'development')
    }), 200

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({'error': 'Method not allowed'}), 405

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f'Server Error: {error}')
    return jsonify({'error': 'Internal server error'}), 500

# Only for development
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 4900))
    app.run(host='0.0.0.0', port=port, debug=app.config['DEBUG'])