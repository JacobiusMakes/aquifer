# Contributing to Aquifer

Thanks for your interest in contributing to Aquifer. This project handles medical data de-identification, so correctness and security are paramount.

## Getting Started

```bash
git clone https://github.com/aquifer-health/aquifer.git
cd aquifer
pip install -e ".[dev]"
python -m spacy download en_core_web_sm  # optional, for NER detection
pytest tests/ -v
```

## Development Workflow

1. Fork the repo and create a branch from `main`
2. Write tests for any new functionality
3. Run the full test suite: `pytest tests/ -v`
4. Submit a PR with a clear description

## What to Contribute

**High-impact areas:**
- New PHI pattern detectors (especially for non-US formats)
- File format extractors (new file types)
- Improvements to detection recall (catch more PHI)
- .aqf format tooling (readers/writers in other languages)
- Documentation and examples

**Guidelines:**
- **Recall over precision**: If your change could miss PHI, it's a regression. Over-detection is acceptable; under-detection is not.
- **No network calls**: Everything must work fully offline.
- **Test with synthetic data**: Never use real patient data in tests. Use the synthetic generator: `python tests/generate_synthetic.py`
- **Token security**: Tokens must be UUIDv4 with zero derivation from source PHI.

## Testing

```bash
# Run all tests
pytest tests/ -v

# Generate synthetic test data
python tests/generate_synthetic.py --count 50 --output tests/fixtures/synthetic/

# Run a batch stress test
aquifer deid tests/fixtures/synthetic/ -o /tmp/aqf_out --vault /tmp/test.aqv --password test --no-ner
```

## Security

If you discover a security vulnerability, **do not open a public issue**. Email security@aquifer.health instead.

## Code of Conduct

Be respectful. This project exists to protect patient privacy. Act accordingly.

## License

By contributing, you agree that your contributions will be licensed under Apache 2.0.
