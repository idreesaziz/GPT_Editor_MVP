#!/bin/bash
# GitHub-based deployment script - much cleaner approach!

set -e

# Configuration - UPDATE THESE VALUES
GITHUB_REPO="https://github.com/idreesaziz/GPT_Editor_MVP.git"  # Update with your repo
VM_NAME="screenwrite-mvp-c2"
ZONE="europe-west1-c"
VM_USER="$USER"
APP_DIR="/opt/fastapi-app"

echo "🚀 Deploying FastAPI app from GitHub..."

# Get VM external IP
VM_IP=$(gcloud compute instances describe $VM_NAME --zone=$ZONE --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
echo "📡 VM IP: $VM_IP"

# Deploy from GitHub repository
echo "📦 Deploying from GitHub repository..."
gcloud compute ssh $VM_USER@$VM_NAME --zone=$ZONE --command="
    echo '🔄 Updating code from GitHub...' && \
    cd $APP_DIR && \
    if [ -d '.git' ]; then
        echo '📥 Pulling latest changes...'
        git pull origin main
    else
        echo '📥 Cloning repository...'
        cd .. && \
        sudo rm -rf fastapi-app && \
        git clone $GITHUB_REPO fastapi-app && \
        sudo chown -R $USER:$USER fastapi-app && \
        cd fastapi-app
    fi && \
    echo '✅ Code updated successfully!'
"

# Install dependencies
echo "🔧 Installing dependencies..."
gcloud compute ssh $VM_USER@$VM_NAME --zone=$ZONE --command="
    cd $APP_DIR && \
    source venv/bin/activate && \
    pip install -r requirements.txt && \
    echo '✅ Dependencies installed successfully!'
"

# Restart the server (kill existing and start new)
echo "🔄 Restarting FastAPI server..."
gcloud compute ssh $VM_USER@$VM_NAME --zone=$ZONE --command="
    cd $APP_DIR && \
    # Kill existing server process
    pkill -f 'uvicorn app.main:app' || true && \
    sleep 2 && \
    # Start new server
    source venv/bin/activate && \
    nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > server.log 2>&1 & \
    sleep 2 && \
    echo '🎉 Server restarted! Check status with: curl http://localhost:8000/'
"

echo ""
echo "🎉 GitHub deployment complete!"
echo "🌐 Your API is available at: http://$VM_IP:8000"
echo "📋 API docs at: http://$VM_IP:8000/docs"
echo ""
echo "📝 To check server logs:"
echo "   gcloud compute ssh $VM_USER@$VM_NAME --zone=$ZONE"
echo "   cd $APP_DIR && tail -f server.log"
echo ""
echo "🔄 To deploy updates:"
echo "   1. Push changes to GitHub"
echo "   2. Run this script again"
