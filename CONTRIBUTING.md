# Contributing

1. `make install`
2. Make your change, with tests. The simulator (`carscan/obd/simulator.py`) and `FakeProvider`
   (`tests/conftest.py`) let you test against a car and an AI without hardware or keys.
3. `scripts/test.sh` must pass (lint, Python tests at 85 %+ coverage, JS checks).
4. Open a pull request describing what changed and how you tested it.

Adding brand coverage (module names, odometer DIDs) is especially welcome. Include a simulator case.
