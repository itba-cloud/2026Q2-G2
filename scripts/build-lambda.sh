#!/usr/bin/env bash
# Builds one Lambda package: build/<name>.zip.
#
#   scripts/build-lambda.sh <name> <source dir>
#   scripts/build-lambda.sh api app/api
#
# The zip is flat: the Lambda's own modules sit next to app/shared's, so
# `from common import ...` resolves from the package root, the same way it
# does in the workers' image. verify.py stays out: it is a check, not code
# that runs in AWS.
#
# Dependencies come from <source dir>/requirements.txt as Linux x86_64 wheels
# for Python 3.12, the Lambda runtime, so the zip works whatever machine
# builds it. The same sources give the same zip, so Terraform sees no change
# when nothing changed.
set -euo pipefail

name="$1"
source_dir="$2"

root="$(cd "$(dirname "$0")/.." && pwd)"
stage="$root/build/lambda/$name"
package="$root/build/$name.zip"

rm -rf "$stage" "$package"
mkdir -p "$stage"

python3 -m pip install --quiet --no-compile --disable-pip-version-check \
  --target "$stage" \
  --implementation cp --python-version 3.12 --only-binary=:all: \
  --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 \
  -r "$root/$source_dir/requirements.txt"

cp "$root"/app/shared/*.py "$stage/"
find "$root/$source_dir" -maxdepth 1 -name '*.py' ! -name verify.py \
  -exec cp {} "$stage/" \;

# Same dates, permissions and order on every machine. zip stores local time,
# so both touch and zip run in UTC.
chmod -R u=rwX,go=rX "$stage"
TZ=UTC find "$stage" -exec touch -h -t 198001010000 {} +
(cd "$stage" && find . -type f | LC_ALL=C sort | TZ=UTC zip -q -X -@ "$package")

printf '%-8s %6s zipped, %6s unzipped\n' "$name" \
  "$(du -h "$package" | cut -f1)" "$(du -sh "$stage" | cut -f1)"
