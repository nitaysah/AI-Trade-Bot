#!/bin/bash

# =================================================================
# AI Trading Bot — Backend Setup Script
# =================================================================

echo "🚀 Starting AI Trading Bot Backend Setup..."

# 1. Verify a supported Python runtime. Prefer the same current runtime used
# by the container image, while allowing supported 3.11/3.12 hosts.
REQUIRED_VERSION="3.11 or newer"
PY_CMD=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &> /dev/null && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' &> /dev/null; then
        PY_CMD="$candidate"
        break
    fi
done
if [ -z "$PY_CMD" ]; then
    echo "❌ Error: Python $REQUIRED_VERSION is required but not found."
    echo "Please install Python 3.11 or newer before running setup."
    exit 1
fi

PYTHON_VERSION=$($PY_CMD -c 'import sys; print(sys.version.split()[0])')
echo "✅ Python $PYTHON_VERSION detected."

# 2. Create virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment (venv) using $PY_CMD..."
    $PY_CMD -m venv venv
else
    echo "✅ Virtual environment already exists."
    if ! venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' &> /dev/null; then
        echo "❌ Existing venv uses Python older than 3.11. Remove it and rerun setup."
        exit 1
    fi
fi

# 3. Install the repository's hash-locked dependency graph.
echo "📥 Installing hash-locked dependencies..."
source venv/bin/activate
python -m pip install --require-hashes -r requirements.lock.txt
python -m pip install --require-hashes --no-deps -r webull-requirements.lock.txt

# 4. Generate .env file if missing
if [ ! -f ".env" ]; then
    echo "📝 Generating .env template..."
    VAULT_KEY="$($PY_CMD -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    cat <<EOT >> .env
ENV=development

# Local encryption key for stored broker credentials. Keep this file private.
VAULT_SECRET_KEY=$VAULT_KEY
VAULT_SECRET_KEY_VERSION=2

# Groq API Key (Sentiment Analysis)
GROQ_API_KEY=your_groq_api_key_here

# Alpaca API Credentials (Broker)
ALPACA_API_KEY=your_alpaca_key_here
ALPACA_SECRET_KEY=your_alpaca_secret_here
ALPACA_PAPER=true

# Massive API Credentials
MASSIVE_API_KEY=your_massive_api_key_here
MASSIVE_BASE_URL=https://api.massive.com
EOT
    unset VAULT_KEY
    echo "✅ .env file created. Please fill in your API keys."
else
    echo "✅ .env file already exists."
fi

echo "✨ Setup complete! You can now run ./start_backend.sh"
