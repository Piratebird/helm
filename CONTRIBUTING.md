# Contributing to Helm

First off, thank you for considering contributing to Helm!

## Setup Environment
1. Clone the repo and navigate to it: `git clone https://github.com/Piratebird/helm.git && cd helm`
2. Set up a virtual environment: `python -m venv venv && source venv/bin/activate`
3. Install dependencies: `pip install -e .[dev]`

## Code Quality
We use `ruff` for linting and formatting, and `mypy` for static type checking.
- To format: `ruff format .`
- To lint: `ruff check .`
- To type check: `mypy src/`

We (mostly i) recommend installing our pre-commit hooks to automate this:
`pip install pre-commit && pre-commit install`

## Testing
We (me and the voices) use `pytest`. To run tests:
`pytest tests/`
