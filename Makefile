lint:
	uv run ruff format .
	uv run ruff check --fix .
	uv run ty check .


# Same checks as `lint`, but reports instead of fixing (what CI runs)
check:
	uv run ruff format --check .
	uv run ruff check .
	uv run ty check .


test:
	uv run pytest


# ------------------------------------------------------------------------------
# The image
# ------------------------------------------------------------------------------
# The terraform output from aws-batch-optimization (`make outputs` there). A
# local copy wins so CI can drop one in from the shared state.
CONFIG := $(firstword $(wildcard config.json $(HOME)/.aws-batch/config.json))
IMAGE_URL ?= $(shell jq -r '.repo_urls.value["gold-rush"]' $(CONFIG))
# Lazy, not `:=`: an immediate assignment calls STS on every make invocation,
# including `make build`, which needs no credentials.
ACCOUNT = $(shell aws sts get-caller-identity --query "Account" --output text)
REGION ?= us-east-2
TAG ?= local

IS_MAIN := $(shell git rev-parse --abbrev-ref HEAD | grep -q ^main$$ && echo true || echo false)

# CI passes buildx cache flags in here; empty locally.
CACHE_FLAGS ?=

BUILD_FLAGS := -t ${IMAGE_URL}:${TAG}
ifeq ($(IS_MAIN),true)
BUILD_FLAGS += -t ${IMAGE_URL}:latest
endif

# `--load` locally, where the point is to have the image; empty in CI, where
# the point is only that it builds.
BUILD_OUTPUT ?= --load

build:
	docker buildx build ${CACHE_FLAGS} ${BUILD_FLAGS} ${BUILD_OUTPUT} .

_ecr_login:
	aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com

push: _ecr_login
	docker buildx build ${CACHE_FLAGS} ${BUILD_FLAGS} --push .


.PHONY: lint check test build _ecr_login push
