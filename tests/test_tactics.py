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

def test_fork_witness_and_unrealized_candidate():
    b=chess.Board("3q4/4k3/8/4N3/8/8/8/4K3 w - - 0 1")
    assert b.is_valid()
    pv=["e5c6","e7f8","c6d8"]
    d=scan(b,pv,chess.WHITE)
    assert d["valid"] and "fork" in d["confirmed"]
    partial=scan(b,pv[:1],chess.WHITE)
    assert "fork" in partial["candidates"] and "fork" not in partial["confirmed"]

def test_skewer_and_discovery_bitboard_paths():
    b=chess.Board("8/4q3/8/4k3/8/8/8/R5K1 w - - 0 1")
    d=scan(b,["a1e1","e5f5","e1e7"],chess.WHITE)
    assert d["valid"] and "skewer" in d["confirmed"]
    b=chess.Board("q3k3/8/8/8/8/B7/8/R3K3 w - - 0 1")
    d=scan(b,["a3b4","e8f8","a1a8"],chess.WHITE)
    assert d["valid"] and "discovered_attack" in d["confirmed"]

def test_same_root_inconsistent_score_does_not_claim_missed():
    b=chess.Board()
    payload={"perspective":"white","best":{"cp":300,"mate":None,"pv":["e2e4"]},"played":{"cp":-300,"mate":None,"pv":["e2e4"]}}
    assert not any(f["kind"] in ("missed","allowed") for f in diagnose(b,chess.Move.from_uci("e2e4"),payload)["findings"])
