from chessbuilder.review import metrics,summarize,line,detail
from chessbuilder.db import migrate,connect
from chessbuilder.importer import parse,store
import chess

def test_accuracy():
    a={"perspective":"white","best":{"cp":200,"mate":None},"played":{"cp":0,"mate":None}}
    m=metrics(a)
    assert m["cp_loss"]==200 and m["accuracy"]<70 and m["win_loss"]>10
    assert summarize([a])["black"]["accuracy"] is None
    a["played"]=a["best"]
    assert metrics(a)["accuracy"]==100
    a["played"]={"cp":None,"mate":-2}
    assert metrics(a)["cp_loss"] is None

def test_line_and_detail(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[White "Alice"]\n[Black "Bob"]\n\n1. e4 e5 *'));store(g,p)
    d=detail(1)
    assert d["moves"][0]["san"]=="e4" and d["metrics"]["white"]["accuracy"] is None
    assert line(chess.Board(),["e2e4","e7e5"])[1]["san"]=="e5"
