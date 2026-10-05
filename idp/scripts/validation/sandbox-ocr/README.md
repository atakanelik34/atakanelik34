# Real OCR in a sandbox without the Debian archive

**Not for production.** CI builds and validates the true default image
(`.github/workflows/idp-ci.yml`, job `images`). This directory exists because
the phase 13 validation sandbox could not reach `deb.debian.org`, so the default
image could only be built with `OCR_PACKAGES=""`.

The workaround layers a self-contained conda-forge Tesseract (5.3.4, `eng`,
`deu`, `osd`) onto that image. The Python code, user, read-only filesystem and
entry points are those of the default image. This exercises the real OCR
runtime path inside the container: TesseractOCREngine → `tesseract`
subprocess → worker → API.

```bash
# 1. Tesseract into /opt/tess (conda-forge is reachable from the sandbox)
curl -L https://conda.anaconda.org/conda-forge/linux-64/micromamba-2.9.0-0.tar.bz2 | tar -xj bin/micromamba
bin/micromamba create -y -p /opt/tess -c conda-forge --override-channels tesseract=5.3.4
find /opt/tess/share/tessdata -name '*.traineddata' ! -name eng.* ! -name deu.* ! -name osd.* -delete
# 2. default image without apt OCR packages, then the OCR layer
docker compose -f docker-compose.yml -f .e2e-override.yml build    # OCR_PACKAGES=""
mkdir -p /tmp/tessctx && cp -a /opt/tess /tmp/tessctx/tess
docker build -t idp-backend:tess -f scripts/validation/sandbox-ocr/Dockerfile /tmp/tessctx
# 3. OCR check inside the container, then the stack with OCR on
docker run --rm -v "$PWD:/src:ro" -e PYTHONPATH=/src/backend -e JWT_SECRET=x \
  idp-backend:tess python /src/scripts/validation/ocr_check.py
docker compose -f docker-compose.yml -f .e2e-override.yml \
  -f scripts/validation/sandbox-ocr/compose.override.yml up -d
backend/.venv/bin/python scripts/validation/stack_validation.py --uploads 100 --pages 500
```
