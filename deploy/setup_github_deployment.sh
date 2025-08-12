#!/bin/bash
# Initial VM setup for GitHub-based deployment

set -e

# Configuration - UPDATE THESE VALUES
GITHUB_REPO="https://github.com/idreesaziz/GPT_Editor_MVP.git"  # Update with your repo
VM_NAME="screenwrite-mvp-c2"
ZONE="europe-west1-c"
VM_USER="$USER"
APP_DIR="/opt/fastapi-app"

echo "🚀 Setting up VM for GitHub-based deployment..."

# Setup VM with all dependencies
echo "🔧 Installing system dependencies..."
gcloud compute ssh $VM_USER@$VM_NAME --zone=$ZONE --command="
    # Update system
    sudo apt update && sudo apt upgrade -y && \
    
    # Install Python and dependencies
    sudo apt install -y python3 python3-pip python3-venv git nginx && \
    
    # Install system dependencies for your app
    sudo apt install -y ffmpeg imagemagick && \
    
    echo '✅ System dependencies installed!'
"

# Clone repository and setup
echo "📦 Cloning repository and setting up environment..."
gcloud compute ssh $VM_USER@$VM_NAME --zone=$ZONE --command="
    # Create and setup app directory
    sudo mkdir -p /opt && \
    cd /opt && \
    sudo rm -rf fastapi-app && \
    sudo git clone $GITHUB_REPO fastapi-app && \
    sudo chown -R $USER:$USER fastapi-app && \
    cd fastapi-app && \
    
    # Create Python virtual environment
    python3 -m venv venv && \
    source venv/bin/activate && \
    pip install --upgrade pip && \
    pip install -r requirements.txt && \
    
    echo '✅ Repository cloned and environment setup complete!'
"

# Start the server for the first time
echo "🚀 Starting FastAPI server..."
gcloud compute ssh $VM_USER@$VM_NAME --zone=$ZONE --command="
    cd $APP_DIR && \
    source venv/bin/activate && \
    nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > server.log 2>&1 & \
    sleep 2 && \
    echo '🎉 Server started! Check status with: curl http://localhost:8000/'
"

# Get VM IP for display
VM_IP=$(gcloud compute instances describe $VM_NAME --zone=$ZONE --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

echo ""
echo "🎉 GitHub-based deployment setup complete!"
echo "🌐 Your API is available at: http://$VM_IP:8000"
echo "📋 API docs at: http://$VM_IP:8000/docs"
echo ""
echo "🔄 To deploy updates in the future:"
echo "   1. Push changes to GitHub: git push origin main"
echo "   2. Run: ./deploy/deploy_from_github.sh"
echo ""
echo "📝 To check server logs:"
echo "   gcloud compute ssh $VM_USER@$VM_NAME --zone=$ZONE"
echo "   cd $APP_DIR && tail -f server.log"
