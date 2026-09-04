#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=== AKVO REAL HARDWARE TESTS ==="
echo
echo "Edit real_hardware_tests/config.json before running."
echo
python3 real_hardware_tests/test_real_hardware.py
