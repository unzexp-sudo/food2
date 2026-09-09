# Handwritten order eval set

5 real handwritten delivery notes used to calibrate the OCR pipeline's
confidence threshold and to validate the `requires_human_review` gate.

| File | Form type | Difficulty | Notes |
|------|-----------|------------|-------|
| 001_two_slips.jpeg | mixed (two notes; right = semi-printed) | high | left slip is partial/cut off |
| 002_songdan_609621.jpeg | printed product list + handwritten qty/price | medium | clean pink slip, 11 lines |
| 003_songdan_609621_edited.jpeg | same as 002, with strikethroughs/edits | medium | line 8 marked "(已关)" cancelled |
| 004_songdan_0102282.jpeg | fully handwritten + corrections | very high | empty cells, crossed-out values, misaligned rows |
| 005_shengxian_XS202504001.jpeg | fully printed form + handwritten weights | low | easiest case; numbers only |

Expected parses live in `expected/*.json` (ground truth from manual read).
The harness in `tests/test_handwritten_eval.py` loads these and runs the
MathValidator + contract checks. Real API extractors (Aliyun/Qwen) need
keys set in the environment; without them the harness validates the
contract and math logic only.

## To add the images
Copy the 5 source jpegs into this folder and rename to the names above:
  001_two_slips.jpeg
  002_songdan_609621.jpeg
  003_songdan_609621_edited.jpeg
  004_songdan_0102282.jpeg
  005_shengxian_XS202504001.jpeg
