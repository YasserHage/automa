# Reproduction image. Builds an isolated environment with the package installed;
# makes no LLM calls itself - anything that reaches a model needs GEMINI_API_KEY (or
# ANTHROPIC_API_KEY) passed at `docker run` time, never baked into the image.
FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -e ".[dev]" \
    && mkdir -p trajectories catalog \
    && touch trajectories/.gitkeep catalog/.gitkeep

# Offline and free by default: the full test suite, no key required. Override the
# command for anything else, e.g.:
#   docker run --rm -e GEMINI_API_KEY automa python -m automa.scripts.hello_llm
CMD ["python", "-m", "pytest"]
