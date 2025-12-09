# Nginx Reverse Proxy Configuration

Optional nginx configuration for running the Exhibition VM Controller API behind a reverse proxy.

## Overview

While the FastAPI controller runs on port 8000 by default, you may want to:
- **Run on port 80/443**: Standard HTTP/HTTPS ports
- **Add HTTPS**: Secure connections (less critical for isolated networks)
- **Host multiple services**: Reverse proxy to multiple backend services
- **Add authentication**: Protect admin endpoints

## When to Use Nginx

**Use nginx if**:
- You want the API accessible on port 80/443
- You're running multiple web services on the host
- You need load balancing or caching
- You want centralized logging

**Skip nginx if**:
- API only accessed from VMs on internal network
- Simple single-service setup
- Don't need port 80/443

**Note**: For most exhibition setups, nginx is optional. The default setup (FastAPI on port 8000, VMs connecting directly) works fine.

## Basic Configuration

### Simple Reverse Proxy

Save as `/etc/nginx/sites-available/exhibition-vm-controller`:

```nginx
server {
    listen 80;
    server_name localhost;

    # API endpoint
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts for long-running operations
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Root endpoint
    location / {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Health check
    location /health {
        access_log off;
        return 200 "OK\n";
        add_header Content-Type text/plain;
    }
}
```

Enable and restart:

```bash
sudo ln -s /etc/nginx/sites-available/exhibition-vm-controller /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## Advanced Configuration

### With HTTPS (Let's Encrypt)

```nginx
# HTTP redirect to HTTPS
server {
    listen 80;
    server_name example.com;
    return 301 https://$server_name$request_uri;
}

# HTTPS server
server {
    listen 443 ssl http2;
    server_name example.com;

    # SSL certificates (from Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # API endpoints
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### With Basic Authentication

Protect admin endpoints:

```bash
# Create password file
sudo apt-get install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd admin
```

```nginx
server {
    listen 80;
    server_name localhost;

    # Public endpoints (heartbeat, status)
    location ~ ^/api/v1/(heartbeat|status)$ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }

    # Protected admin endpoints
    location /api/ {
        auth_basic "Exhibition VM Controller Admin";
        auth_basic_user_file /etc/nginx/.htpasswd;

        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
    }
}
```

### With Access Control

Restrict access by IP:

```nginx
server {
    listen 80;
    server_name localhost;

    # Allow local network only
    allow 192.168.122.0/24;  # Libvirt default network
    allow 127.0.0.1;          # Localhost
    deny all;

    location / {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
    }
}
```

### With Rate Limiting

Prevent abuse:

```nginx
# Add to http block in /etc/nginx/nginx.conf
http {
    # Rate limiting zone
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

    # ... rest of config
}
```

```nginx
server {
    listen 80;
    server_name localhost;

    location /api/ {
        # Apply rate limiting (allow burst of 20)
        limit_req zone=api_limit burst=20 nodelay;

        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
    }

    location / {
        proxy_pass http://127.0.0.1:8000/;
    }
}
```

## Installation

### Install Nginx

```bash
sudo apt-get update
sudo apt-get install nginx
```

### Configure

1. **Create configuration file**:
   ```bash
   sudo nano /etc/nginx/sites-available/exhibition-vm-controller
   ```

2. **Copy configuration** from examples above

3. **Test configuration**:
   ```bash
   sudo nginx -t
   ```

4. **Enable site**:
   ```bash
   sudo ln -s /etc/nginx/sites-available/exhibition-vm-controller \
              /etc/nginx/sites-enabled/
   ```

5. **Restart nginx**:
   ```bash
   sudo systemctl restart nginx
   ```

### Verify

```bash
# Test locally
curl http://localhost/api/v1/status

# From VM (adjust IP)
curl http://192.168.122.1/api/v1/status
```

## Logging

### Enable Access Logging

```nginx
server {
    listen 80;
    server_name localhost;

    # Custom log format
    access_log /var/log/nginx/exhibition-vm-access.log;
    error_log /var/log/nginx/exhibition-vm-error.log;

    location / {
        proxy_pass http://127.0.0.1:8000/;
    }
}
```

### View Logs

```bash
# Access log
sudo tail -f /var/log/nginx/exhibition-vm-access.log

# Error log
sudo tail -f /var/log/nginx/exhibition-vm-error.log
```

## Troubleshooting

### 502 Bad Gateway

Backend (FastAPI) not running:

```bash
# Check FastAPI is running
curl http://localhost:8000/api/v1/status

# Check systemd service
sudo systemctl status exhibition-vm-controller

# Check nginx can connect
sudo tail -f /var/log/nginx/error.log
```

### 403 Forbidden

Permission or access control issue:

```bash
# Check nginx error log
sudo tail /var/log/nginx/error.log

# Verify IP allowlist if using access control
```

### Timeout Errors

Increase timeouts:

```nginx
location /api/ {
    proxy_connect_timeout 120s;
    proxy_send_timeout 120s;
    proxy_read_timeout 120s;
    # ... rest of config
}
```

### Certificate Errors (HTTPS)

```bash
# Test certificate
sudo nginx -t

# Check certificate files exist
ls -l /etc/letsencrypt/live/example.com/

# Renew Let's Encrypt certificate
sudo certbot renew
```

## Performance Tuning

### Enable Compression

```nginx
server {
    listen 80;
    server_name localhost;

    # Enable gzip compression
    gzip on;
    gzip_types application/json text/plain;
    gzip_min_length 1000;

    location / {
        proxy_pass http://127.0.0.1:8000/;
    }
}
```

### Buffer Settings

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000/api/;

    # Buffer settings
    proxy_buffering on;
    proxy_buffer_size 4k;
    proxy_buffers 8 4k;
    proxy_busy_buffers_size 8k;
}
```

## Security Recommendations

For production/public-facing deployments:

1. **Use HTTPS**: Even on internal networks
2. **Authenticate admin endpoints**: Use basic auth or IP restrictions
3. **Rate limiting**: Prevent API abuse
4. **Keep nginx updated**: Security patches
5. **Monitor logs**: Watch for suspicious activity
6. **Firewall**: Restrict access at network level

## Typical Exhibition Setup

For most exhibition environments, you don't need nginx:

```
┌─────────────────────────┐
│  Physical Host          │
│  ┌───────────────────┐ │
│  │ FastAPI :8000     │ │  ← Direct access, no nginx
│  └───────────────────┘ │
│         ↑               │
│         │ 192.168.122.x │
│  ┌───────────────────┐ │
│  │ VM (Guest)        │ │
│  │ Sends to :8000    │ │
│  └───────────────────┘ │
└─────────────────────────┘
```

This is simpler and works well for isolated networks.

## Author

Marc Schütze (mschuetze@zkm.de)
ZKM | Center for Art and Media Karlsruhe

## License

MIT License - See repository LICENSE file
