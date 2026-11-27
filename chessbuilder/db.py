import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles(id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL);
INSERT OR IGNORE INTO profiles VALUES(1,'Meu perfil');
CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, source TEXT NOT NULL, username TEXT NOT NULL COLLATE NOCASE, cursor TEXT, UNIQUE(source,username));
CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL, account_id INTEGER REFERENCES accounts(id), played_at TEXT, speed TEXT, white TEXT NOT NULL, black TEXT NOT NULL, white_rating INTEGER, black_rating INTEGER, result TEXT NOT NULL, player_color TEXT CHECK(player_color IN ('white','black')), opening TEXT, eco TEXT, termination TEXT, initial_fen TEXT NOT NULL, pgn TEXT NOT NULL, time_control TEXT, UNIQUE(source,external_id));
CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY, fen TEXT NOT NULL, history_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS moves(game_id INTEGER REFERENCES games(id) ON DELETE CASCADE, ply INTEGER NOT NULL, position_id INTEGER REFERENCES positions(id), san TEXT NOT NULL, uci TEXT NOT NULL, fen_after TEXT NOT NULL, clock REAL, PRIMARY KEY(game_id,ply));
CREATE TABLE IF NOT EXISTS evaluations(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL REFERENCES games(id), settings TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','running','paused','done','failed','cancelled')), next_ply INTEGER NOT NULL DEFAULT 1, error TEXT, priority INTEGER NOT NULL DEFAULT 0, UNIQUE(game_id,settings));
CREATE TABLE IF NOT EXISTS move_analysis(job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE, ply INTEGER, payload TEXT NOT NULL, PRIMARY KEY(job_id,ply));
CREATE TABLE IF NOT EXISTS findings(id INTEGER PRIMARY KEY, game_id INTEGER REFERENCES games(id), ply INTEGER, theme TEXT, confidence REAL, evidence TEXT);
CREATE TABLE IF NOT EXISTS exercises(id INTEGER PRIMARY KEY, position_id INTEGER REFERENCES positions(id), source_game INTEGER REFERENCES games(id), solution TEXT NOT NULL, theme TEXT);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, exercise_id INTEGER REFERENCES exercises(id), played_at TEXT NOT NULL, answer TEXT, correct INTEGER, elapsed_ms INTEGER);
CREATE TABLE IF NOT EXISTS repertoires(id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL, pgn TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS games_date ON games(played_at);
CREATE INDEX IF NOT EXISTS games_account ON games(account_id);
"""
def db_path():
    root = Path(os.environ.get("CHESSBUILDER_DATA_DIR", Path.home()/".local/share/chessbuilder"))
    root.mkdir(parents=True, exist_ok=True)
    return root/"chessbuilder.sqlite3"

@contextmanager
def connect(path=None):
    conn = sqlite3.connect(path or db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

def migrate(path=None):
    with connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise RuntimeError("Database schema is newer than this application")
        if version == 0:
            conn.executescript("BEGIN IMMEDIATE;"+SCHEMA+"PRAGMA user_version=1; COMMIT;")
