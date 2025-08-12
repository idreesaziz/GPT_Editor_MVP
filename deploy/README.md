# FastAPI GCP Deployment - GitHub Based

## 🚀 Clean GitHub-Based Deployment

**No more manual file uploads!** Deploy directly from GitHub.

### Quick Start Commands

```bash
# 1. Push deployment files to GitHub
git add deploy/ .github/
git commit -m "Add GitHub-based deployment"
git push origin main

# 2. Create GCP VM
gcloud compute instances create fastapi-server \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --machine-type=e2-medium \
    --zone=us-central1-a \
    --tags=http-server,https-server \
    --boot-disk-size=20GB

gcloud compute firewall-rules create allow-fastapi \
    --allow tcp:8000 \
    --source-ranges 0.0.0.0/0

# 3. One-time setup (clones from GitHub)
./deploy/setup_github_deployment.sh

# 4. Future deployments (just push and run)
git push origin main
./deploy/quick_deploy.sh
```

## 🛠 **Available Files:**

- **`setup_github_deployment.sh`** - One-time VM setup (clones from GitHub)
- **`quick_deploy.sh`** - Fast deployment after git push
- **`deploy_from_github.sh`** - Full deployment with dependencies  
- **`setup_production.sh`** - Optional: systemd service setup
- **`github_actions_setup.md`** - Auto-deployment guide

## ⚡ **Super Simple Workflow:**

```bash
# Make changes, commit, push
git add .
git commit -m "Updated feature"
git push origin main

# Deploy in 10 seconds
./deploy/quick_deploy.sh
```

Your API will be at `http://YOUR_VM_IP:8000`!
