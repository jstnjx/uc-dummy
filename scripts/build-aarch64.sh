#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_VER="${PYTHON_VER:-3.11.13-0.7.0}"
VERSION="$(python3 -c 'import json; print(json.load(open("driver.json"))["version"])')"
NAME="uc-intg-dummy-v${VERSION}-aarch64"

rm -rf build dist artifacts *.spec

docker run --rm \
  --platform=linux/arm64/v8 \
  --user="$(id -u):$(id -g)" \
  -v "$ROOT:/workspace" \
  "docker.io/unfoldedcircle/r2-pyinstaller:${PYTHON_VER}" \
  bash -c '
    set -e
    cd /workspace
    PYTHON_VERSION=$(python --version | cut -d" " -f2 | cut -d. -f1,2)
    pip install --user -r requirements.txt
    PYTHONPATH="$HOME/.local/lib/python${PYTHON_VERSION}/site-packages:$PYTHONPATH" \
      pyinstaller --clean --onedir --name intg-dummy src/driver.py -y
  '

mkdir -p artifacts
mv dist/intg-dummy artifacts/bin
mv artifacts/bin/intg-dummy artifacts/bin/driver
cp driver.json LICENSE artifacts/
printf 'v%s\n' "$VERSION" > artifacts/version.txt

tar czf "${NAME}.tar.gz" -C artifacts .
sha256sum "${NAME}.tar.gz" > "${NAME}.sha256"

echo "Created: ${NAME}.tar.gz"
echo "SHA256:  ${NAME}.sha256"
