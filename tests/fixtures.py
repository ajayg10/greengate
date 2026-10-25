"""Shared fixture data: a realistic baseline pipeline's jobs, as the GitLab jobs API returns them."""

BASELINE_JOBS = [
    {"id": 11, "name": "lint", "stage": "lint", "status": "success", "duration": 62.0, "queued_duration": 3.0,
     "tag_list": [], "runner": {"description": "3-blue.saas-linux-small-amd64.runners-manager.gitlab.com/default"}},
    {"id": 12, "name": "unit-tests", "stage": "test", "status": "success", "duration": 95.0, "queued_duration": 2.0,
     "tag_list": [], "runner": {"description": "2-green.saas-linux-small-amd64.runners-manager.gitlab.com/default"}},
    {"id": 13, "name": "unit-tests-again", "stage": "test", "status": "success", "duration": 93.0,
     "queued_duration": 2.0, "tag_list": [], "runner": None},
    {"id": 14, "name": "integration-tests", "stage": "test-integration", "status": "success", "duration": 101.0,
     "queued_duration": 4.0, "tag_list": ["saas-linux-small-amd64"], "runner": {}},
    {"id": 15, "name": "build-image", "stage": "build", "status": "success", "duration": 180.0,
     "queued_duration": 5.0, "tag_list": [], "runner": {}},
    {"id": 16, "name": "gg-deploy-decide", "stage": "deploy", "status": "success", "duration": 30.0,
     "queued_duration": 1.0, "tag_list": [], "runner": {}},
    {"id": 17, "name": "gg-smoke", "stage": "test", "status": "manual", "duration": None, "tag_list": [],
     "runner": None},
    {"id": 18, "name": "gg-sci-meter", "stage": ".post", "status": "running", "duration": None, "tag_list": [],
     "runner": None},
]

BASELINE_APP_YML = """\
# greengate-profile: baseline
lint:
  stage: lint
  image: python:3.12
  script:
    - pip install --no-cache-dir -r requirements-dev.txt
    - ruff check .

unit-tests:
  stage: test
  image: python:3.12
  script:
    - pip install --no-cache-dir -r requirements-dev.txt
    - pytest -q tests --cov=app

unit-tests-again:
  stage: test
  image: python:3.12
  script:
    - pip install --no-cache-dir -r requirements-dev.txt
    - pytest -q tests --cov=app

integration-tests:
  stage: test-integration
  image: python:3.12
  script:
    - pip install --no-cache-dir -r requirements-dev.txt
    - python scripts/smoke_local.py

build-image:
  stage: build
  image: docker:27.3.1
  script:
    - docker build -f app/Dockerfile -t img .
"""

OPTIMIZED_APP_YML = """\
# greengate-profile: optimized
.py:
  image: python:3.12-slim
  variables:
    PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"
  cache:
    key:
      files: [requirements-dev.txt]
    paths: [.cache/pip]
  rules:
    - changes: [app/**/*, tests/**/*, requirements-dev.txt, .gitlab/ci/app.yml]

lint:
  extends: .py
  stage: lint
  needs: []
  script:
    - pip install -r requirements-dev.txt
    - ruff check .

unit-tests:
  extends: .py
  stage: test
  needs: []
  script:
    - pip install -r requirements-dev.txt
    - pytest -q tests --cov=app

integration-tests:
  extends: .py
  stage: test-integration
  needs: [unit-tests]
  script:
    - pip install -r requirements-dev.txt
    - python scripts/smoke_local.py

build-image:
  stage: build
  image: docker:27.3.1
  rules:
    - changes: [app/**/*]
  script:
    - docker build --cache-from img:latest -f app/Dockerfile -t img .
"""
