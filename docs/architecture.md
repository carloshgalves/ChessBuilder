# Architecture

Single local profile; accounts identify public data sources. SQLite is the system of record. Every game retains the original PGN; moves reference contextual positions, including history relevant to repetition. Jobs persist the next ply and analysis settings. Evaluation payloads are versioned by engine and search parameters. Future diagnostic and exercise modules consume immutable source evidence. The frontend communicates with the loopback API through a Vite proxy. No authentication or remote deployment is required.
