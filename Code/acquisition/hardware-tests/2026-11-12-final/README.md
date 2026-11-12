# Newest-only clean recalibration audit

User directs primary fit from newest 170710 ONLY: earlier sessions are known flawed. All older unique complete recordings are cross-session diagnostics, not definitive acceptance. Original/relabelled 114212 are identical raw data and cannot provide independent evidence. Partial/empty/unlabelled data are inventory only.

No fresh clean prospectively collected evaluation session exists. Accidental glances have not been identified per sample and are not silently removed.

## Result

34 of 40 movement cue windows contain ADC clipping. Only six windows are clip-free, and only two support the required full recovered plateau at tau .15. The other clean windows have late responses and do not contain the full .85-second plateau window. At least five clean plateau fits are required; that threshold was not lowered.

**No candidate model was produced. EOG dashboard approval remains false.**

Do not enable EOG dashboard control from this failed fit. Capture smaller unsaturated movements promptly after cues; keep at least .85 seconds of stable side gaze after movement and collect a separate independent evaluation. Alternatively extend guided hold duration in a future code change. No raw samples, accidental glances or labels were removed.

## All uploaded recordings

| File | Samples | Complete | Duplicate |
|---|---:|---|---|
| calibration-20261112-113140.csv | 3500 | False |  |
| calibration-20261112-113548.csv | 0 | False |  |
| calibration-20261112-113613.csv | 0 | False | calibration-20261112-113548.csv |
| calibration-20261112-113628.csv | 0 | False | calibration-20261112-113548.csv |
| calibration-20261112-113906.csv | 22084 | False |  |
| calibration-20261112-114114.csv | 4101 | False |  |
| calibration-20261112-114212-relabelled.csv | 128972 | True |  |
| calibration-20261112-114212.csv | 128972 | True | calibration-20261112-114212-relabelled.csv |
| calibration-20261112-122332.csv | 129510 | True |  |
| calibration-20261112-125337.csv | 129577 | True |  |
| calibration-20261112-170112-555.csv | 2638 | False |  |
| calibration-20261112-170710-083.csv | 130224 | True |  |
| capture-20261111-170007.csv | 75004 | False |  |
| capture-20261111-173504.csv | 9537 | False |  |
| signal-check-run3-1729.csv | 11836 | False |  |
| signal-check.csv | 11836 | False | signal-check-run3-1729.csv |

## Deployed default replay — diagnostic only

```json
{
  "passed": false,
  "independent": false,
  "recording_id": "cabdd20b03819e0677a4d08407d90cf85d73b52438ae998be4733a30da78f37e",
  "model_id": "d0b5f74e20d95b9addfea652f465b5f0d031e3d490996b50c57fa6f60aa7101e",
  "metrics": {
    "trials": 40,
    "hits": 22,
    "missed": [
      5,
      7,
      8,
      9,
      10,
      11,
      13,
      15,
      17,
      29,
      34,
      37,
      38,
      43,
      47,
      58,
      60,
      73
    ],
    "recall": 0.55,
    "precision": 0.55,
    "protocol_precision": 0.611,
    "false_during_protocol": 14,
    "false_after_blink_cues": 2,
    "false_per_minute": 2.13,
    "natural_events": 4,
    "natural_per_minute": 2.0,
    "natural_seconds": 120.012,
    "minutes": 8.56
  }
}
```

The deployed model was not refitted or approved using this diagnostic. Full per-trial clipping counts and inventory identities are in `recalibration-audit.json`.
