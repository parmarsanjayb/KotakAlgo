#!/bin/bash
# =========================================================================
# Hostinger VPS Automated Deployment Script for Kotak Securities Algo Bot
# =========================================================================

echo "=========================================================="
echo " 🚀 Initializing VPS Deployment Setup..."
echo "=========================================================="

# 1. Update package lists
echo "1. Updating system package lists..."
sudo apt update -y

# 2. Install Python, Pip, SQLite, and Nginx
echo "2. Installing Python3, SQLite3, and Nginx..."
sudo apt install -y python3 python3-pip python3-venv sqlite3 nginx git

# 3. Create project directory
echo "3. Creating application directory /var/www/KotakAlgo..."
sudo mkdir -p /var/www/KotakAlgo
sudo chown -R $USER:$USER /var/www/KotakAlgo

# 4. Copy files (Assuming script is run from the project root containing files)
echo "4. Copying files to deployment directory..."
sudo cp -rf * /var/www/KotakAlgo/
cd /var/www/KotakAlgo

# 5. Create Python Virtual Environment & Install Dependencies
echo "5. Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install flask pyotp requests pandas numpy "git+https://github.com/Kotak-Neo/Kotak-neo-api-v2.git#egg=neo_api_client"

# 6. Create systemd Service file for background daemonization
echo "6. Configuring background service (kotakalgo.service)..."
sudo bash -c 'cat > /etc/systemd/system/kotakalgo.service <<EOF
[Unit]
Description=Kotak Securities Neo Algo Console
After=network.target

[Service]
User=root
WorkingDirectory=/var/www/KotakAlgo
ExecStart=/var/www/KotakAlgo/venv/bin/python app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF'

# 7. Enable and restart the system service
echo "7. Restarting Kotak Algo background daemon..."
sudo systemctl daemon-reload
sudo systemctl restart kotakalgo
sudo systemctl enable kotakalgo

# 8. Configure Nginx Web Server / Reverse Proxy
echo "8. Configuring Nginx web server reverse proxy..."
sudo bash -c 'cat > /etc/nginx/sites-available/kotakalgo <<EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
        proxy_connect_timeout 300s;
    }
}
EOF'

# Enable Nginx config and restart server
sudo ln -sf /etc/nginx/sites-available/kotakalgo /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl restart nginx

echo "=========================================================="
echo " 🎉 SETUP COMPLETED SUCCESSFULLY!"
echo " Your trading server is now running 24/7."
echo " Access from your mobile phone using: http://<YOUR_VPS_IP>"
echo "=========================================================="
