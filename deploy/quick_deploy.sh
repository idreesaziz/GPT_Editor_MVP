#!/bin/bash
# Quick deployment script - after initial setup

set -e

GITHUB_REPO="https://github.com/idreesaziz/GPT_Editor_MVP.git"
VM_NAME="screenwrite-mvp-c2" 
ZONE="europe-west1-c"
VM_USER="$USER"
APP_DIR="/opt/fastapi-app"

echo "🚀 Quick GitHub deployment..."

# Get current commit for deployment tracking
COMMIT_HASH=$(git rev-parse --short HEAD)
echo "📝 Deploying commit: $COMMIT_HASH"

# Deploy from GitHub
gcloud compute ssh $VM_USER@$VM_NAME --zone=$ZONE --command="
    cd $APP_DIR && \
    echo '📥 Pulling latest changes...' && \
    git pull origin main && \
    source venv/bin/activate && \
    pip install -r requirements.txt && \
    # Graceful restart
    pkill -f 'uvicorn app.main:app' || true && \
    sleep 2 && \
    nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > server.log 2>&1 & \
    sleep 2 && \
    echo '✅ Deployment complete!'
"

VM_IP=$(gcloud compute instances describe $VM_NAME --zone=$ZONE --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
echo "🌐 API: http://$VM_IP:8000 | Docs: http://$VM_IP:8000/docs"
