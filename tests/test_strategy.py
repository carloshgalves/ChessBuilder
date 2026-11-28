import chess
from chessbuilder.strategy import side_features,compare_lines,diagnose_strategy
def test_pawn_structure_and_passers():
    b=chess.Board("4k3/8/8/P7/P7/8/8/4K3 w - - 0 1")
    f=side_features(b,chess.WHITE)
    assert f["pawn_structure"]==-4 and f["passed_pawns"]==2
def test_open_file_persistent_contrast():
    b=chess.Board("4k3/pp3ppp/8/8/8/8/PP3PPP/R3K2R w KQ - 0 1")
    good=["a1d1","e8f8","h1g1","f8e8","g1f1"]
    bad=["a1b1","e8f8","h1g1","f8e8","g1f1"]
    findings=compare_lines(b,good,bad)
    assert any(f["theme"]=="strategy:open_files" for f in findings)
    assert not compare_lines(b,good,bad[:1])
    assert not compare_lines(b,good,good)
def test_shallow_abstention():
    b=chess.Board()
    a={"perspective":"white","settings":{"level":"quick"},"best":{"cp":200,"mate":None,"pv":["e2e4"]},"played":{"cp":0,"mate":None,"pv":["a2a3"]}}
    d=diagnose_strategy(b,chess.Move.from_uci("a2a3"),a)
    assert d["status"]=="abstain" and not d["findings"]

def test_concrete_open_file_witness():
    from chessbuilder.strategy import contextual_contrast
    b=chess.Board("4k3/pp3ppp/8/8/8/8/PP3PPP/R3K2R w KQ - 0 1")
    good=["a1d1","e8f8","h1g1"];bad=["a1b1","e8f8","h1g1"]
    c=contextual_contrast(b,good,bad,"open_files")
    assert c["ply"]==3 and c["reference"]["Torres em colunas abertas"]==["d1"]
    assert c["compared"]["Torres em colunas abertas"]==[]
    assert c["reference_san"][0]=="Rd1"
