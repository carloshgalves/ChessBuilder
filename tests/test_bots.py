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
