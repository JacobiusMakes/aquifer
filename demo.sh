#!/usr/bin/env bash
set -e

echo "=== Aquifer Demo ==="
echo ""

# Check if installed
if ! python -m aquifer --version &>/dev/null; then
    echo "Installing Aquifer..."
    pip install -e . >/dev/null 2>&1
fi

VERSION=$(python -m aquifer --version 2>&1)
echo "Using $VERSION"
echo ""

# Create temp directory for demo
DEMO_DIR=$(mktemp -d)
VAULT="$DEMO_DIR/demo.aqv"
trap "rm -rf $DEMO_DIR" EXIT

# Step 1: Show the sample file (contains PHI)
echo "--- Step 1: Sample file with PHI ---"
echo ""
head -8 tests/fixtures/sample_clinical_note.txt
echo "  ..."
echo ""

# Step 2: De-identify
echo "--- Step 2: De-identify → .aqf file ---"
echo ""
python -m aquifer deid tests/fixtures/sample_clinical_note.txt \
    -o "$DEMO_DIR/output.aqf" \
    --vault "$VAULT" \
    --password demo \
    --verbose --no-ner 2>&1 | head -25
echo ""

# Step 3: Inspect the .aqf (no PHI visible)
echo "--- Step 3: Inspect .aqf file (zero PHI) ---"
echo ""
python -m aquifer inspect "$DEMO_DIR/output.aqf"
echo ""

# Step 4: Rehydrate (restore PHI from vault)
echo "--- Step 4: Rehydrate (PHI restored from vault) ---"
echo ""
python -m aquifer rehydrate "$DEMO_DIR/output.aqf" \
    --vault "$VAULT" \
    --password demo 2>&1 | head -8
echo "  ..."
echo ""

# Step 5: Vault stats
echo "--- Step 5: Vault stats ---"
echo ""
python -m aquifer vault stats "$VAULT" --password demo
echo ""

# Step 6: Batch processing
echo "--- Step 6: Batch process all fixtures ---"
echo ""
python -m aquifer deid tests/fixtures/ \
    -o "$DEMO_DIR/batch/" \
    --vault "$VAULT" \
    --password demo --no-ner
echo ""

# Final vault stats
python -m aquifer vault stats "$VAULT" --password demo
echo ""
echo "=== Demo complete. All temp files cleaned up. ==="
