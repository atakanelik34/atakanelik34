# SANDBOX WORKAROUND — not a production image.
#
# The default backend image installs Tesseract from the Debian archive
# (OCR_PACKAGES). Where that archive is unreachable (the phase 13 sandbox), this
# layers a self-contained conda-forge Tesseract build onto the default image
# built with OCR_PACKAGES="". Everything else — Python code, user, read-only
# runtime, entry points — is the default image. Use it only to exercise the
# real OCR runtime path; CI validates the true default image.
#
#   build context: a directory containing `tess/` = a conda prefix installed at
#   /opt/tess (see README.md next to this file)
ARG BASE=idp-backend:dev
FROM ${BASE}
USER root
COPY tess /opt/tess
RUN ln -s /opt/tess/bin/tesseract /usr/local/bin/tesseract
# The conda build needs the explicit data path (with trailing slash).
ENV TESSDATA_PREFIX=/opt/tess/share/tessdata/
USER idp
