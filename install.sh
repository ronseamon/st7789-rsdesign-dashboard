#!/bin/bash
set -e

echo "Creating virtual environment..."
python3 -m venv st7789-venv
source st7789-venv/bin/activate

echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "DONE."
echo "Activate with: source st7789-venv/bin/activate"
