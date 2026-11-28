import chess
from chessbuilder.db import migrate
from chessbuilder.bots import Settings,create,move,Command,advance,detail,choose_candidate
def test_seeded_candidate():
    b=chess.Board()
    scores=[{"cp":20,"mate":None,"pv":["e2e4"]},{"cp":0,"mate":None,"pv":["d2d4"]}]
    s=Settings().model_dump()
    assert choose_candidate(b,scores,s,9)==choose_candidate(b,scores,s,9)
def test_resumable_legal_game(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g=create(Settings(minutes=5,increment=3))
    g=move(g["id"],Command(revision=g["revision"],uci="e2e4"))
    assert not g["human_turn"] and len(g["moves"])==1
    monkeypatch.setattr("chessbuilder.bots.motor_candidates",lambda *args:[{"cp":10,"mate":None,"pv":["e7e5"]}])
    g=advance(g["id"],Command(revision=g["revision"]))
    assert g["human_turn"] and len(g["moves"])==2 and detail(g["id"])["fen"]==g["fen"]

def test_calibration_excludes_incomplete():
    from chessbuilder.calibrate_bots import summarize
    r=summarize([{"result":"win","policy_moves":10,"captures":2,"checks":1,"candidate_cp_loss":100},{"result":"*","policy_moves":10,"captures":0,"checks":0,"candidate_cp_loss":0}])
    assert r["completed"]==1 and r["incomplete"]==1 and r["score"]==1 and r["capture_rate"]==0.1

def test_calibration_real_stockfish_resume(tmp_path,monkeypatch):
    import pytest
    import chess.engine
    from chessbuilder.calibrate_bots import run
    from chessbuilder.engine import executable
    from chessbuilder.db import connect
    path=executable()
    if not path:pytest.skip("Stockfish unavailable")
    with chess.engine.SimpleEngine.popen_uci(path) as e:
        option=e.options.get("UCI_Elo")
        if not option:pytest.skip("Stockfish lacks UCI_Elo")
        elo=max(option.min,min(1600,option.max))
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    r=run("universal",800,elo,2,42,20)
    assert r["status"]=="complete" and len(r["records"])==2
    assert r["records"][0]["seed"]==r["records"][1]["seed"]
    assert all(g["pgn"] for g in r["records"])
    assert run("universal",800,elo,2,42,20,resume=r["report_id"])["records"]==r["records"]
    with connect() as c:assert c.execute("SELECT COUNT(*) FROM bot_calibration").fetchone()[0]==1
