#!/bin/bash

# =================================================================
# AI Trading Bot — Backend Setup Script
# =================================================================

echo "🚀 Starting AI Trading Bot Backend Setup..."

# 1. Verify Python 3.13
REQUIRED_VERSION="3.13"

# Find Python 3.13 binary
if command -v python3.13 &> /dev/null; then
    PY_CMD="python3.13"
elif command -v python3 &> /dev/null && [[ $(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")') == "$REQUIRED_VERSION" ]]; then
    PY_CMD="python3"
else
    echo "❌ Error: Python $REQUIRED_VERSION is required but not found."
    echo "Please install Python 3.13 (e.g., 'brew install python@3.13' on Mac)."
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
fi

# 3. Upgrade pip and install dependencies
echo "📥 Installing dependencies from requirements.txt..."
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# 4. Generate .env file if missing
if [ ! -f ".env" ]; then
    echo "📝 Generating .env template..."
    cat <<EOT >> .env
# Groq API Key (Sentiment Analysis)
GROQ_API_KEY=your_groq_api_key_here

# Alpaca API Credentials (Broker)
ALPACA_API_KEY=your_alpaca_key_here
ALPACA_SECRET_KEY=your_alpaca_secret_here
ALPACA_PAPER=true
EOT
    echo "✅ .env file created. Please fill in your API keys."
else
    echo "✅ .env file already exists."
fi

echo "✨ Setup complete! You can now run ./start_backend.sh"
