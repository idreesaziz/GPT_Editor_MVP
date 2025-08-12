#!/bin/bash
# Production setup script - run this on the VM after deployment

set -e

echo "🔧 Setting up production environment..."

# Create systemd service
sudo cp /opt/fastapi-app/deploy/fastapi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fastapi
sudo systemctl start fastapi

echo "✅ FastAPI service installed and started"

# Setup Nginx reverse proxy (optional but recommended)
sudo tee /etc/nginx/sites-available/fastapi > /dev/null <<EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # Handle WebSocket connections if needed
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

# Enable Nginx site
sudo ln -sf /etc/nginx/sites-available/fastapi /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo "✅ Nginx reverse proxy configured"

# Show service status
echo ""
echo "📊 Service Status:"
sudo systemctl status fastapi --no-pager
echo ""
echo "🌐 Your API is now available at:"
echo "   http://$(curl -s ifconfig.me)/ (via Nginx)"
echo "   http://$(curl -s ifconfig.me):8000/ (direct)"
echo ""
echo "📋 Useful commands:"
echo "   sudo systemctl status fastapi    # Check status"
echo "   sudo systemctl restart fastapi   # Restart service"
echo "   sudo journalctl -u fastapi -f    # View logs"
