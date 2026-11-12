# Latest raw-waveform provisional fit

Only the latest complete session was fitted. Raw data were not modified and accidental glances were not removed. The historical time constant was fixed at 0.15 s rather than estimated from too few clean plateaus.

Selected conservative best-F1 parameters: threshold 1750 recovered counts, hold 0.8 seconds.
Measured on the fitting recording: 28/40 hits, 70% recall, 63.6% precision,
11 false events during the protocol and 5 during natural use (2.5/minute).
This balances misses and false commands better than the highest-recall candidate.
The highest-recall alternative (hold 0.15 seconds) reached 31/40 hits but only
41.9% precision and 12.5 natural false events/minute; it was not selected.

This is a same-recording fit, not independent validation. Model `validation` remains
null. The user has explicitly accepted imperfect prototype dashboard use; enabling
it requires the application's visibly labelled provisional mode and does not make
the model approved. Actual ADC rail contact and display-range cutoff are different;
brief rail contact was accepted using the existing runtime tolerance.
