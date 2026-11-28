from fastapi.testclient import TestClient
from chessbuilder.db import connect, migrate, db_path
from chessbuilder.main import app

def test_migration_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path))
    migrate()
    with connect() as conn:
        conn.execute("UPDATE profiles SET name='Teste' WHERE id=1")
    migrate()
    assert db_path().exists()
    with TestClient(app) as client:
        assert client.get("/api/health").json()=={"status":"ok","schema":7}
        assert client.get("/api/profile").json()["name"]=="Teste"
