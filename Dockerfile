# What runs on Batch: `gold-rush pull <venue> <league>`, and nothing else.
#
# 3.13-bookworm, pinned by digest so a rebuild is the same interpreter. The
# full image rather than -slim, as cassandra's is, because it already has git,
# which uv needs to clone the three git dependencies (call-it-what-you-want,
# endgame, endgame-aws) at their pinned revs -- so there's no apt step.
FROM python@sha256:227b6570d6ee07061ae6ca2eb04dedfb6d2b34045835f343065b9869e4d427ea

# uv from PyPI rather than copied out of its ghcr.io image: the one install
# path that works anywhere pip does.
RUN pip install --no-cache-dir uv==0.8.17 \
    && useradd --uid 1000 --create-home app

USER app
WORKDIR /home/app/gold-rush

# The interpreter above, never one uv downloads; bytecode compiled at build
# time so a job doesn't pay for it on every start.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/home/app/gold-rush/.venv/bin:${PATH}

# Dependencies before the code, so editing gold_rush/ doesn't reinstall them.
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY --chown=app:app README.md ./
COPY --chown=app:app gold_rush ./gold_rush
RUN uv sync --locked --no-dev

# Where endgame_aws's `Config.init_from_file` finds the bucket -- the second of
# its two candidates, and the only one that works for an installed package
# (the first is the package root, which is site-packages). Required at build
# time, so a missing bucket fails the build rather than every job. CI writes
# it from the shared stack's state; see .github/workflows/image.yml.
COPY --chown=app:app config.json /home/app/.aws-batch/config.json

ENTRYPOINT ["gold-rush"]
CMD ["report"]
