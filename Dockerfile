# Stage 1: Build Backend
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS backend-builder
WORKDIR /flare-ai-social
# Copy all backend source files and configuration into the image.
COPY . .
# Install dependencies using uv sync
RUN uv sync --all-extras
RUN apt-get update && apt-get install -y ffmpeg

# Stage 2: Final Image
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
# Copy the virtual environment and necessary files from the builder stage.
COPY --from=backend-builder /flare-ai-social/.venv ./.venv
COPY --from=backend-builder /flare-ai-social/src ./src
COPY --from=backend-builder /flare-ai-social/pyproject.toml .
COPY --from=backend-builder /flare-ai-social/README.md .
COPY cookies.txt /app/cookies.txt

RUN chmod +x /app/.venv/bin/twspace_dl
RUN sed -i '1s,#!/flare-ai-social/.venv/bin/python,#!/app/.venv/bin/python,' /app/.venv/bin/twspace_dl
RUN apt-get update && apt-get install -y ffmpeg

RUN . ./.venv/bin/activate

# Expose port 80
EXPOSE 80

# Command to start the Twitter bot
CMD ["uv", "run", "start-bots"]