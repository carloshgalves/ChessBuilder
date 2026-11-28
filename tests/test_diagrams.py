import pytest
pytest.importorskip("pypdfium2")
np=pytest.importorskip("numpy")
from chessbuilder.diagrams import Region,square,make_templates,classify
import chess

def test_square_orientation():
    assert square(0,"white")==chess.A8 and square(0,"black")==chess.H1

def test_exact_templates_and_abstention():
    b=chess.Board()
    labels={p:i for i,p in enumerate(".PNBRQKpnbrqk")}
    # Distinct synthetic visual fingerprints, one pixel block per class.
    cells=[]
    for i in range(64):
        p=b.piece_at(square(i,"white"))
        v=np.zeros(1024,dtype=np.float32)
        k=labels[p.symbol() if p else "."]
        v[k*64:(k+1)*64]=1
        cells.append(v)
    t=make_templates(cells,b.fen(),"white")
    result=classify(cells,t,"white","w")
    assert result["fen"].split()[0]==b.board_fen()
    assert not result["uncertain"] and not result["approved"]
    cells[0]=np.ones(1024,dtype=np.float32)
    assert "a8" in classify(cells,t,"white","w")["uncertain"]
