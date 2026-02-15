# 🚀 Universal Deployment Agent

**Deploy any web app to a Linux server with one command. No DevOps experience needed.**

Give it your GitHub repo → it figures out everything else → your app is live.

```
                    ┌─────────────────┐
  Your GitHub Repo  │                 │  Your App is Live
  ───────────────►  │  Deploy Agent   │  ───────────────►  yoursite.com ✓
  + 5 lines of YAML │                 │
                    └─────────────────┘
```

---

## What Does This Do?

Imagine you built a website or web app. Now you need to put it on the internet so people can use it. Normally, that requires:

- Renting a server (like AWS EC2)
- Installing a web server (Apache/Nginx)
- Installing your programming language (Node.js, Python, PHP, etc.)
- Setting up a database (MySQL, PostgreSQL)
- Configuring everything to work together
- Making sure it stays running

**This agent does ALL of that for you automatically.** You just tell it where your code is, and it handles the rest.

---

## Supported App Types

| Type | Examples | What It Auto-Detects |
|------|----------|---------------------|
| **Node.js** | Express, NestJS, Fastify | Port, database, React/Vue frontend |
| **Python** | Django, Flask, FastAPI | Framework, database, WSGI/ASGI server |
| **PHP** | Laravel, WordPress, Symfony | PHP version, extensions, database |
| **Next.js** | Next.js SSR apps | Build settings, port |
| **Ruby** | Rails, Sinatra | Bundler, Puma, database |
| **Go** | Gin, Echo, Fiber | Build from source, port |
| **Java** | Spring Boot, Quarkus | Maven/Gradle, JDK version |
| **Rust** | Actix, Axum, Rocket | Cargo build, port |
| **.NET** | ASP.NET Core | SDK version, Kestrel |
| **Static** | React, Vue, Angular, Hugo | Build tool, output directory |

---

## Quick Start (3 Steps)

### What You Need

- A **Linux server** (Ubuntu, Debian, CentOS, Fedora) — like an AWS EC2 instance
- **SSH access** to that server
- A **GitHub repository** with your app's code

### Step 1: Get the Agent on Your Server

SSH into your server and run:

```bash
git clone https://github.com/AbdurRazzaq2004/Php-Fpm-Automation-Agent.git
cd Php-Fpm-Automation-Agent
```

### Step 2: Tell It About Your App

Open the file `services.yml` and edit it. Here's all you need:

```yaml
service_name: my-app
domain: 123.45.67.89
repo_url: https://github.com/your-username/your-app.git
branch: main
```

> **That's it!** The agent figures out the language, framework, database, and everything else automatically.

Replace the values:
| Field | What to Put |
|-------|-------------|
| `service_name` | Any name for your app (no spaces, e.g. `my-blog`) |
| `domain` | Your server's IP address or domain name |
| `repo_url` | Your GitHub repository URL |
| `branch` | Usually `main` or `master` |

### Step 3: Deploy!

```bash
sudo ./deploy.sh
```

**When it's done, open your browser and go to `http://your-server-ip` — your app is live!**

Here's what the agent does behind the scenes:

```
 ✅ Install Docker (if not already installed)
 ✅ Detect your app's language and framework
 ✅ Install the correct runtime (Node.js, Python, PHP, etc.)
 ✅ Install your app's dependencies
 ✅ Build your app (if needed)
 ✅ Set up a database (if your app needs one)
 ✅ Configure a web server (Apache/Nginx)
 ✅ Start your app and keep it running
 ✅ Health check — verify it's working
```

---

## Real-World Examples

### Deploy a Node.js App (Express + React + MySQL)

```yaml
service_name: todo-app
domain: 54.91.12.14
language: node
repo_url: https://github.com/your-username/todo-mern-app.git
branch: main
web_server: apache
```

**What the agent does automatically:**
- Installs Node.js 20
- Runs `npm install` for the backend
- Detects the `client/` folder → builds the React frontend
- Detects MySQL from `package.json` → installs MySQL, creates database & user
- Reads `.env.example` → generates `.env` with secure passwords
- Sets up Apache to serve React + proxy API to Express
- Starts Express with PM2 (auto-restarts if crashes)

### Deploy a Python App

```yaml
service_name: my-blog
domain: blog.example.com
language: python
repo_url: https://github.com/your-username/django-blog.git
branch: main
web_server: nginx
```

### Deploy a PHP Laravel App

```yaml
service_name: my-laravel-app
domain: app.example.com
repo_url: https://github.com/your-username/laravel-app.git
branch: main
web_server: nginx
```

> For PHP apps, you don't even need `language:` — it's auto-detected from `composer.json`.

### Deploy a Static Site (React/Vue/Hugo)

```yaml
service_name: my-portfolio
domain: portfolio.example.com
language: static
repo_url: https://github.com/your-username/react-portfolio.git
branch: main
```

---

## Configuration Guide

### Minimal Config (Let the Agent Decide Everything)

```yaml
service_name: my-app
domain: example.com
repo_url: https://github.com/user/repo.git
branch: main
```

### Full Config (Override Anything You Want)

```yaml
service_name: my-app
domain: example.com
repo_url: https://github.com/user/repo.git
branch: main
deploy_path: /var/www/my-app        # Where to put the code on server
language: node                       # node, python, php, ruby, go, java, rust, dotnet, static
web_server: apache                   # apache or nginx

# Private repository? Add your GitHub token:
# pat_token: ghp_your_github_token_here

# SSL (HTTPS):
# enable_ssl: true

# Override auto-detected settings (usually not needed):
# runtime_version: "20"             # Node 20, Python 3.12, PHP 8.2, etc.
# app_port: 3000                    # Port your app listens on
# start_command: "node server.js"   # How to start your app
# build_command: "npm run build"    # How to build your app

# Extra environment variables:
# environment_vars:
#   SECRET_KEY: "my-secret"
#   API_URL: "https://api.example.com"
```

### All Config Fields

| Field | Required? | What It Does |
|-------|-----------|-------------|
| `service_name` | ✅ Yes | A unique name for your app (used in paths and service names) |
| `domain` | ✅ Yes | Your domain name or server IP address |
| `repo_url` | ✅ Yes | GitHub URL of your app's code |
| `branch` | ✅ Yes | Git branch to deploy (usually `main`) |
| `deploy_path` | No | Where to put code (default: `/var/www/<service_name>`) |
| `language` | No | Programming language — auto-detected if not set |
| `web_server` | No | `nginx` (default) or `apache` |
| `pat_token` | No | GitHub Personal Access Token for private repos |
| `enable_ssl` | No | `true` for HTTPS with free Let's Encrypt certificate |
| `runtime_version` | No | Override language version (e.g., `"20"` for Node 20) |
| `app_port` | No | Port your app listens on (auto-detected) |
| `start_command` | No | Override how your app starts |
| `build_command` | No | Override how your app builds |
| `environment_vars` | No | Extra environment variables for your app |

---

## How-To Guides

### Deploy a Private Repository

If your code is in a **private** GitHub repo:

1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Click **"Generate new token (classic)"**
3. Check the **`repo`** scope
4. Copy the token and add it to your config:

```yaml
service_name: my-private-app
domain: example.com
repo_url: https://github.com/your-username/private-repo.git
branch: main
pat_token: ghp_xxxxxxxxxxxxxxxxxxxx
```

### Enable HTTPS (SSL)

1. Make sure your **domain name** points to your server (DNS A record)
2. Add `enable_ssl: true` to your config:

```yaml
service_name: my-app
domain: mysite.com          # Must be a real domain, not an IP
enable_ssl: true
repo_url: https://github.com/user/repo.git
branch: main
```

3. Run `sudo ./deploy.sh` — free SSL certificate from Let's Encrypt is set up automatically.

### Deploy Multiple Apps on One Server

Use the `services:` list format:

```yaml
services:
  - service_name: frontend
    domain: mysite.com
    language: static
    repo_url: https://github.com/user/frontend.git
    branch: main

  - service_name: api
    domain: api.mysite.com
    language: node
    repo_url: https://github.com/user/backend.git
    branch: main

  - service_name: blog
    domain: blog.mysite.com
    language: python
    repo_url: https://github.com/user/blog.git
    branch: main
```

Each app gets its own isolated environment — they won't interfere with each other.

### Redeploy After Code Changes

Just push your code to GitHub, then run:

```bash
sudo ./deploy.sh
```

It pulls the latest code, rebuilds, and restarts your app.

---

## Commands

| Command | What It Does |
|---------|-------------|
| `sudo ./deploy.sh` | Deploy your app |
| `sudo ./deploy.sh validate` | Check config for errors (without deploying) |
| `sudo ./deploy.sh deploy --dry-run` | Simulate deployment (no actual changes) |
| `sudo ./deploy.sh deploy --verbose` | Deploy with detailed output |

---

## Smart Auto-Detection

The agent reads your project files and automatically figures out what your app needs:

| What It Detects | How |
|----------------|-----|
| **Language** | `package.json` → Node.js, `requirements.txt` → Python, `composer.json` → PHP |
| **Framework** | Express, Django, Laravel, Rails, Spring Boot — from your dependencies |
| **Database** | `mysql2` in package.json → MySQL, `psycopg2` → PostgreSQL |
| **Port** | Reads `.env.example` and source code for port configuration |
| **Frontend** | Detects `client/` or `frontend/` folders with React/Vue builds |
| **Runtime version** | `.nvmrc`, `.python-version`, `go.mod`, `composer.json` |

**You don't need to configure any of this — it just works.**

---

## Troubleshooting

### My app deployed but shows an error page

Check the logs:

```bash
# Node.js apps (PM2)
sudo pm2 logs

# Python/Go/Java apps (systemd)
sudo journalctl -u app-<your-service-name> -n 50 --no-pager

# Web server error logs
sudo tail -50 /var/log/apache2/<your-service-name>-error.log
sudo tail -50 /var/log/nginx/<your-service-name>-error.log
```

### Database connection error

```bash
# Check if database is running
sudo systemctl status mysql
sudo systemctl status postgresql

# Check your app's .env file
sudo cat /var/www/<your-service-name>/.env | grep DB_
```

### Permission denied

Make sure you run with `sudo`:

```bash
sudo ./deploy.sh
```

### Docker not found

The agent installs Docker automatically. If it fails, run the installer first:

```bash
sudo ./install.sh
```

---

## Project Structure

```
Php-Fpm-Automation-Agent/
├── deploy.sh          ← Run this to deploy (main command)
├── install.sh         ← Installs Docker (run once)
├── services.yml       ← Your app config (edit this!)
├── deployer.py        ← The deployment engine
├── Dockerfile         ← Agent container
├── config/            ← Config parsing & validation
├── modules/           ← Automation modules
│   ├── apache.py      ← Apache setup
│   ├── nginx.py       ← Nginx setup
│   ├── database.py    ← MySQL/PostgreSQL auto-setup
│   ├── git.py         ← Git clone & pull
│   ├── ssl.py         ← Let's Encrypt SSL
│   ├── permissions.py ← File security
│   └── runtimes/      ← Language installers
│       ├── node_runtime.py
│       ├── python_runtime.py
│       ├── go_runtime.py
│       └── ... (all 10 languages)
└── examples/          ← Example configs
    ├── node-app.yml
    ├── python-django-app.yml
    ├── laravel-app.yml
    └── ... (every language)
```

---

## Safety

The agent is designed to be safe and non-destructive:

- ✅ **Never deletes databases** or existing data
- ✅ **Backs up configs** before making changes
- ✅ **Tests web server config** before reloading (prevents broken sites)
- ✅ **Uses reload, not restart** — zero downtime
- ✅ **Isolates each app** — separate users, permissions, and logs
- ✅ **Auto-generates secure passwords** for databases
- ✅ **Adds security headers** to every site
- ✅ **Blocks access** to `.env`, `.git`, and other sensitive files

---

## FAQ

**Q: Do I need to know Docker?**
No. Docker runs in the background. You never touch it directly.

**Q: Can I deploy to any cloud provider?**
Yes — AWS, DigitalOcean, Google Cloud, Azure, Linode, Vultr, or any Linux server.

**Q: Does it work with Nginx and Apache?**
Yes. Set `web_server: nginx` or `web_server: apache`.

**Q: What if my repo is private?**
Add `pat_token: ghp_...` to your config (see [Private Repos](#deploy-a-private-repository)).

**Q: Does it set up the database?**
Yes. It auto-detects which database your app needs, installs it, creates the database, creates a user with a strong password, and updates your `.env` file — all automatically.

**Q: What Linux versions work?**
Ubuntu (20.04–24.04), Debian (11–12), CentOS/RHEL (8–9), Fedora, Amazon Linux.

**Q: Is it free?**
Yes, 100% free and open source.

---

## One-Liner Deploy

SSH into a **fresh** Linux server and run:

```bash
git clone https://github.com/AbdurRazzaq2004/Php-Fpm-Automation-Agent.git /tmp/deployer \
  && cd /tmp/deployer \
  && nano services.yml \
  && sudo ./deploy.sh
```

Edit `services.yml` with your app details → save → done. Everything installs from scratch.

---

<p align="center">
  <b>Built by <a href="https://github.com/AbdurRazzaq2004">Abdur Razzaq</a></b>
</p>
