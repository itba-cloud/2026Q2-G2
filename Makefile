# Builds what Terraform deploys (one zip per Lambda in build/, and the
# workers' image) and runs Terraform, which lives in infra/, the way the
# workflows do. `make` alone builds everything.
#
#   make lambdas        build/api.zip, build/auth_callback.zip, build/ingest.zip,
#                       build/db_migrator.zip, build/invite.zip,
#                       build/alert_matcher.zip, build/day_close.zip and
#                       build/precompute.zip
#   make worker-image   the lgdc-worker image (linux/amd64, like Fargate),
#                       tagged with a hash of its sources
#   make worker-push    pushes that image to ECR, only if ECR lacks the tag
#   make portal         builds the portal into app/frontend/dist
#   make clean          removes build/
#
#   make init           creates the state bucket if missing and runs terraform init
#   make plan           builds the Lambdas and writes the plan to build/tfplan,
#                       with the workers' image tag
#   make apply          creates the data and portal buckets if missing and
#                       applies build/tfplan
#   make portal-publish syncs the portal into its bucket, after the apply
#   make bootstrap-platform-admin
#                       the platform admin from BOOTSTRAP_EMAIL and
#                       BOOTSTRAP_PASSWORD, after the apply; nothing if the
#                       email is empty
#   make ingest         runs the SEPA ingestion now and shows its result, what
#                       the run-ingest workflow does; SEPA_API_URL, if set,
#                       replaces the proxy Deploy configured
#   make destroy        destroys everything Terraform created, then the buckets
#   make lint           terraform fmt -check, validate and tflint, no AWS needed
#   make verify         the checks against the deployed stack (tools/verify),
#                       in their own virtualenv; SUITES="portal roles" runs
#                       only those. Not part of CI
#
# On an empty account, worker-push goes before plan: the plan points the
# workers at an image tag that must already be in ECR.
#
# The Terraform targets other than lint, plus worker-push, portal-publish,
# bootstrap-platform-admin, ingest and verify, need AWS credentials in the
# environment (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN).

# Also the ECR repository's name: local.worker_name in infra/locals.tf.
WORKER_IMAGE ?= lgdc-worker

TERRAFORM := terraform -chdir=infra
PLAN_FILE := $(CURDIR)/build/tfplan
VERIFY_VENV := build/verify-venv

SHARED := $(wildcard app/shared/*.py) app/shared/requirements.txt scripts/build-lambda.sh

# A component's modules, minus verify.py, which no package carries.
sources = $(filter-out %/verify.py,$(wildcard $(1)/*.py)) $(1)/requirements.txt

# What goes into the workers' image, the same files Dockerfile.dockerignore
# lets through. The tag is a hash of them: it changes only when the image
# would, so Deploy pushes only then, and the plan changes the task
# definition only then.
WORKER_SOURCES := $(sort app/batch/Dockerfile app/batch/Dockerfile.dockerignore \
	$(wildcard app/shared/*.py) app/shared/requirements.txt $(call sources,app/batch/worker))
WORKER_IMAGE_TAG := $(shell sha256sum $(WORKER_SOURCES) | sha256sum | cut -c1-12)

.DEFAULT_GOAL := build
.PHONY: build lambdas worker-image worker-push portal portal-publish \
	bootstrap-platform-admin ingest clean init plan apply destroy lint verify

build: lambdas worker-image

lambdas: build/api.zip build/auth_callback.zip build/ingest.zip build/db_migrator.zip \
	build/invite.zip build/alert_matcher.zip build/day_close.zip build/precompute.zip

# A zip is rebuilt only when its sources change.
build/api.zip: $(SHARED) $(call sources,app/api)
	scripts/build-lambda.sh api app/api

build/auth_callback.zip: $(SHARED) $(call sources,app/auth_callback)
	scripts/build-lambda.sh auth_callback app/auth_callback

build/ingest.zip: $(SHARED) $(call sources,app/batch/ingest)
	scripts/build-lambda.sh ingest app/batch/ingest

build/db_migrator.zip: $(SHARED) $(call sources,app/db_migrator)
	scripts/build-lambda.sh db_migrator app/db_migrator

build/invite.zip: $(SHARED) $(call sources,app/invite)
	scripts/build-lambda.sh invite app/invite

build/alert_matcher.zip: $(SHARED) $(call sources,app/batch/alert_matcher)
	scripts/build-lambda.sh alert_matcher app/batch/alert_matcher

build/day_close.zip: $(SHARED) $(call sources,app/batch/day_close)
	scripts/build-lambda.sh day_close app/batch/day_close

build/precompute.zip: $(SHARED) $(call sources,app/batch/precompute)
	scripts/build-lambda.sh precompute app/batch/precompute

# The context is all of app/; app/batch/Dockerfile.dockerignore keeps it down
# to what the image needs. Docker reads that file only with BuildKit, the
# default builder since Docker 23. --provenance=false keeps the image a
# single manifest: with containerd's image store Docker otherwise pushes an
# index under the same tag after the manifest, which the repository's
# immutable tags reject (400 on the manifest's PUT).
worker-image:
	docker build --platform linux/amd64 --provenance=false -f app/batch/Dockerfile \
		-t $(WORKER_IMAGE):$(WORKER_IMAGE_TAG) app

# The zips first: if the repository is missing, the script creates it with a
# targeted apply, and the configuration reads their hashes.
worker-push: lambdas
	scripts/push-worker-image.sh $(WORKER_IMAGE) $(WORKER_IMAGE_TAG)

# The same build for every account: the portal reads where the API and the
# Hosted UI are from config.json, which Terraform writes into the bucket.
portal:
	cd app/frontend && npm ci && npm run build

# --delete drops what an older build left, all but config.json, which is
# Terraform's. index.html and config.json are asked for on every visit; the
# hashed assets never change, so they are cached for good.
portal-publish: portal
	bucket="$$($(TERRAFORM) output -raw portal_bucket_name)" && \
	aws s3 sync app/frontend/dist "s3://$$bucket" --delete --exclude config.json \
		--exclude index.html --cache-control "public, max-age=31536000, immutable" && \
	aws s3 cp app/frontend/dist/index.html "s3://$$bucket/index.html" \
		--cache-control no-cache

# After the apply: the user pool is Terraform's, the admin is not (the
# script says why).
bootstrap-platform-admin:
	scripts/bootstrap-platform-admin.sh

# After the apply, like the cron: the function reads the proxy's URL from its
# configuration, or from SEPA_API_URL here.
ingest:
	scripts/run-ingest.sh

clean:
	rm -rf build

init:
	infra/init-backend.sh

# PLAN_FLAGS lets Validate plan with -lock=false: a read-only plan must not
# fail because a Deploy holds the lock.
plan: lambdas
	$(TERRAFORM) plan -input=false $(PLAN_FLAGS) \
		-var=worker_image_tag=$(WORKER_IMAGE_TAG) -out="$(PLAN_FILE)"

# The data and portal buckets are the one thing outside Terraform
# (scripts/buckets.sh says why): created before the apply, deleted after the
# destroy.
apply:
	scripts/buckets.sh create
	$(TERRAFORM) apply -input=false "$(PLAN_FILE)"

# Destroy plans too, and the plan reads the zips' hashes.
destroy: lambdas
	$(TERRAFORM) destroy -input=false -auto-approve \
		-var=worker_image_tag=$(WORKER_IMAGE_TAG)
	scripts/buckets.sh delete

# Its own data directory: after make init, infra/.terraform points at the
# remote state, and even init -backend=false reads it there, with AWS
# credentials. Lint needs none.
lint: export TF_DATA_DIR := $(CURDIR)/build/lint-terraform
lint:
	$(TERRAFORM) fmt -check -recursive
	$(TERRAFORM) init -input=false -backend=false
	$(TERRAFORM) validate
	tflint --chdir=infra --init
	tflint --chdir=infra --recursive --config "$(CURDIR)/infra/.tflint.hcl"

# Rebuilt only when the requirements change.
$(VERIFY_VENV)/installed: tools/verify/requirements.txt
	python3 -m venv $(VERIFY_VENV)
	$(VERIFY_VENV)/bin/pip install --quiet --disable-pip-version-check -r $<
	touch $@

# Against the stack Deploy left: reads terraform output, so after make init.
verify: $(VERIFY_VENV)/installed
	$(VERIFY_VENV)/bin/python tools/verify/main.py $(SUITES)
