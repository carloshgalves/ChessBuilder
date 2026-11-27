import chess
from chessbuilder.db import migrate,connect
from chessbuilder.repertoire import graph,key,seed,detail,prepare,Prepare,create,Repertoire,update
from chessbuilder.training import start_attempt,answer,Answer
import json

def test_transposition():
    g=graph('[Result "*"]\n\n1. Nf3 d5 2. d4 *\n\n[Result "*"]\n\n1. d4 d5 2. Nf3 *')
    a=chess.Board();b=chess.Board()
    for san in ("Nf3","d5","d4"):a.push_san(san)
    for san in ("d4","d5","Nf3"):b.push_san(san)
    assert key(a)==key(b) and key(a) in g["nodes"]

def test_caro_recall_and_edit(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    rid=seed()["id"];assert seed()["id"]==rid
    assert detail(rid)["edges"]>10
    ids=prepare(rid,Prepare())["item_ids"];assert ids
    a=start_attempt(ids[0])
    with connect() as c:m=json.loads(c.execute("SELECT solution FROM training_items WHERE id=?",(ids[0],)).fetchone()[0])[0][0]
    assert answer(a["id"],Answer(uci=m,expected_ply=0))["status"]=="passed"
    assert prepare(rid,Prepare())["item_ids"]==ids
    update(rid,Repertoire(name="new",color="black",pgn="1. e4 e5 *"))
    with connect() as c:assert c.execute("SELECT COUNT(*) FROM training_items WHERE active=1").fetchone()[0]==0

def test_line_branches_legal():
    from chessbuilder.repertoire import lines
    g=graph("1. e4 e5 (1... c5) 2. Nf3 *")
    b=chess.Board()
    result=lines(g,b,9)
    assert ("e2e4","e7e5","g1f3") in result
    for pv in result:
        q=b.copy()
        for u in pv:q.push_uci(u)
        assert len(pv)%2==1
