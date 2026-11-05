# CI/CD for Customer360 AI

## Pull requests
- `ci.yml` runs lint + tests + frontend/backend builds on every PR.

## Main branch
- `deploy.yml` runs tests, builds Docker images, and prepares deployment
  artifacts (no secrets/credentials are ever pushed).

Both workflows install dependencies, run the test suites, and verify the
production builds compile before merging or releasing.