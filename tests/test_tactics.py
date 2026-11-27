import chess
from chessbuilder.tactics import scan,diagnose,phase,legal_line,LABELS

def test_mate_and_invalid_line():
    b=chess.Board("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    assert "mate" in scan(b,["f7g7"],chess.WHITE)["confirmed"]
    assert not scan(b,["f7f8","invalid"],chess.WHITE)["valid"]
    assert not scan(b,["a1a2"],chess.WHITE)["valid"]

def test_fork_candidate_and_quiet_negative():
    b=chess.Board("4k3/8/3q4/8/4N3/8/8/4K3 w - - 0 1")
    # Nc5 attacks neither king nor queen together.
    quiet=scan(b,["e4c5"],chess.WHITE)
    assert "fork" not in quiet["confirmed"]
    b=chess.Board("4k3/8/3q4/8/8/4N3/8/4K3 w - - 0 1")
    # Nf5 attacks d6, not the king; one target is not a fork.
    assert "fork" not in scan(b,["e3f5"],chess.WHITE)["candidates"]
    b=chess.Board("4k3/8/3q4/8/3N4/8/8/4K3 w - - 0 1")
    # Nf5 attacks d6 and e7 only, so no assertion of a fork.
    assert "fork" not in scan(b,["d4f5"],chess.WHITE)["confirmed"]

def test_pv_contrast_and_abstention():
    b=chess.Board("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    played=chess.Move.from_uci("f7a2")
    payload={"perspective":"white","best":{"cp":None,"mate":1,"pv":["f7g7"]},"played":{"cp":0,"mate":None,"pv":["f7a2"]}}
    d=diagnose(b,played,payload)
    assert any(f["theme"]=="mate" and f["kind"]=="missed" for f in d["findings"])
    payload["played"]["pv"]=["f7g7"]
    assert diagnose(b,played,payload)["status"]=="abstain"
    payload["played"]["pv"]=["f7a2"]
    payload["perspective"]="black"
    assert diagnose(b,played,payload)["status"]=="abstain"

def test_phase():
    assert phase(chess.Board())=="opening"
    assert phase(chess.Board("7k/8/8/8/8/8/4P3/4K3 w - - 0 1"))=="endgame"
