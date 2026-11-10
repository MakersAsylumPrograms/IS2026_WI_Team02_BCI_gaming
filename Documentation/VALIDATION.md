# Software validation — 10 November 2026

Completed on Windows with Python 3.12, Flask 3.1.3, Waitress 3.0.2 and pytest 9.2.0.

- **15 automated Python tests passed.** Four request types, acknowledgement and
  resolution, idempotency, concurrency, role/CSRF checks, malformed input,
  database failure, persistence, command expiration/deduplication, boot changes,
  consumer leases, lost navigation and buffer overflow were exercised.
- **Real HTTP interoperability passed.** The Python device simulator sent NEXT
  and SELECT through a temporary Waitress listener using the firmware JSON contract.
- **Headless Microsoft Edge integration passed.** Separate patient/staff sessions
  completed request/acknowledge/resolve; a deliberately lost response was retried
  after refresh without duplication; offline recovery, exclusive input consumption,
  and device-driven navigation passed without JavaScript page errors.
- Patient and nurse screenshots were visually inspected. Python compilation,
  JavaScript syntax checks, OpenAPI JSON parsing and Git whitespace checks passed.

Test servers used temporary databases and test-only credentials and were stopped
after testing. No patient records or production credentials were provisioned by
these tests.

Not yet verified: Arduino compilation for the selected ESP32 board, flash/RAM use,
physical GPIO wiring, joystick debounce, EOG acquisition/classification, isolated
hardware setup, two physical laptops, and the two-second feedback target on the
team's LAN. Complete these using INTEGRATION.md and WIRING_HANDOFF.md before the
hardware demonstration. The ESP32 adapter deliberately contains no assumed pinout.
