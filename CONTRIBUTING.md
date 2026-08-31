# Contributing to Helm

First off, thank you for considering contributing to Helm!

## Setup Environment
1. Clone the repo and navigate to it: `git clone https://github.com/Piratebird/helm.git && cd helm`
2. Set up a virtual environment: `python -m venv venv && source venv/bin/activate`
3. Install dependencies: `pip install -e .[dev]`

## Branching Strategy
To maintain good repository hygiene, please avoid committing directly to the `main` branch. 
Instead, create a dedicated branch for your work (i was commiting to main way too much lmao):
- For features: `git checkout -b feature/your-feature-name`
- For bug fixes: `git checkout -b fix/your-bug-fix-name`
- For chores/docs: `git checkout -b chore/your-chore-name`

Once your work is complete, push your branch and open a Pull Request against `main`.

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
