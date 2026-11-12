# Provisional double-blink selection

Replay source: calibration-20261112-170710-083.csv, unchanged.
Fixed detector: filtered-pulse threshold 180 counts, duration 25–300 ms,
same-polarity paired starts 180–750 ms apart, one-second cooldown.
Opposite-polarity rebound does not replace a pending first blink.

18/20 DOUBLE_BLINK trials produced SELECT. Events by onset label:
NEUTRAL 2, DOUBLE_BLINK 18, REST 2, CENTER 4, BLINK 9, RIGHT 2,
LEFT 2, NATURAL 19. The natural-use section is two minutes, so false
selection is substantial (9.5/minute). This replay is in-sample diagnostic
evidence, not independent approval. Threshold sweeps 250/350/500/700 did
not yield reliable separation; no claim of validated blink accuracy is made.

Enabled only with the explicit provisional EOG override requested for prototype
operation. Strict mode does not activate this unevaluated SELECT detector.
Real HTTP bridge tests verify EOG SELECT is transmitted as SELECT, while CYCLE
retains navigation mapping and the joystick button retains SELECT.
