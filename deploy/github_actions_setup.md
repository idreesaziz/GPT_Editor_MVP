# GitHub Actions Setup for Automatic Deployment

## 🔐 Setup Service Account for GitHub Actions

### 1. Create Service Account in GCP

```bash
# Create service account
gcloud iam service-accounts create github-actions \
    --description="Service account for GitHub Actions deployment" \
    --display-name="GitHub Actions"

# Grant necessary permissions
gcloud projects add-iam-policy-binding your-project-id \
    --member="serviceAccount:github-actions@your-project-id.iam.gserviceaccount.com" \
    --role="roles/compute.instanceAdmin"

gcloud projects add-iam-policy-binding your-project-id \
    --member="serviceAccount:github-actions@your-project-id.iam.gserviceaccount.com" \
    --role="roles/compute.osLogin"

# Create and download key
gcloud iam service-accounts keys create github-actions-key.json \
    --iam-account=github-actions@your-project-id.iam.gserviceaccount.com
```

### 2. Add Secret to GitHub Repository

1. Go to your GitHub repository
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Name: `GCP_SA_KEY`
5. Value: Copy the entire contents of `github-actions-key.json`

### 3. Update Workflow File

Edit `.github/workflows/deploy.yml`:
- Replace `your-gcp-project-id` with your actual GCP project ID
- Update VM_NAME and ZONE if different

### 4. Test Automatic Deployment

```bash
# Make a change and push
echo "# Test change" >> README.md
git add .
git commit -m "Test automatic deployment"
git push origin main
```

The GitHub Action will automatically:
1. Trigger on push to main branch
2. SSH into your VM
3. Pull latest code
4. Install dependencies  
5. Restart the server
6. Run health check

### 5. Manual Deployment Trigger

You can also trigger deployments manually:
1. Go to **Actions** tab in GitHub
2. Select **Deploy to GCP VM** workflow
3. Click **Run workflow**

## 🔍 Monitoring Deployments

- **GitHub Actions**: See deployment status in the Actions tab
- **Server Logs**: SSH to VM and run `tail -f /opt/fastapi-app/server.log`
- **Health Check**: Visit `http://YOUR_VM_IP:8000/docs`
