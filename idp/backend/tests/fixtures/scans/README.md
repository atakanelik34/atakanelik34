# Real scanned fixtures for OCR validation

The repository contains no real scanned document yet: the OCR checks in CI use
`tests/fixtures/files.py::scan_like_pdf`, a synthetic page (rendered text with
skew, noise, blur and JPEG compression). That shows Tesseract is installed,
wired and reading pages in the default image. It is not evidence of accuracy on
real scans.

To add a real scan (from a flatbed or MFP, at 200–300 dpi):

1. Use a document that contains **no personal or confidential data**, such as
   a printed sample invoice you created yourself.
2. Save it as `<name>.pdf` (or `.png`, `.jpg`, `.tif`) in this directory.
3. Next to it, add `<name>.expected.txt` with one word or phrase per line that
   OCR must find (case-insensitive).

`scripts/validation/ocr_check.py` (run in CI inside the default backend image,
with Tesseract) and the `@needs_tesseract` tests pick up every pair found here.
