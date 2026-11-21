FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS site
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY index.html tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts ./
COPY public public
COPY src src
ENV VITE_PILOT_HISTORY=1
RUN npm run build

FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache
COPY scripts scripts
COPY tests/fixtures/rnmca_dj1_no2.json tests/fixtures/eea_dj1_no2.json fixtures/
COPY --from=site /app/dist dist
USER 65534:65534
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "scripts.history_api", "--host", "0.0.0.0"]
