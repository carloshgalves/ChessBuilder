import io,zipfile
import pytest
from chessbuilder.champions import unpack,CHAMPIONS,catalog
from chessbuilder.historical import Corpus,import_corpus,games
from chessbuilder.db import migrate,connect
def test_zip_and_catalog(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    assert len(CHAMPIONS)==22 and len(catalog())==22
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,"w") as z:z.writestr("Gukesh.pgn",'[White "Gukesh"]\n[Black "Ding"]\n[Date "2024.12.01"]\n[ECO "B12"]\n[Result "1-0"]\n\n1. e4 c6 1-0')
    raw=unpack(stream.getvalue())
    body=Corpus(edition="Champion:Gukesh",provenance="https://www.pgnmentor.com/players/Gukesh.zip",pgn=raw)
    r=import_corpus(body);assert r["added"]==1
    assert import_corpus(body)["duplicates"]==1
    assert next(x for x in catalog() if x["slug"]=="Gukesh")["imported"]==1
    assert len(games(corpus_id=r["corpus_id"],opening="B12",limit=50,offset=0))==1
    assert not games(corpus_id=r["corpus_id"],start="2025-01-01",limit=50,offset=0)
    with connect() as c:assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]==0
    with zipfile.ZipFile(stream:=io.BytesIO(),"w") as z:z.writestr("a.pgn","x");z.writestr("b.pgn","y")
    with pytest.raises(ValueError):unpack(stream.getvalue())

def test_download_rejects_unknown_and_imports_mock_archive(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from fastapi import HTTPException
    from chessbuilder.champions import download
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,"w") as z:z.writestr("Fischer.pgn",'[White "Fischer"]\n[Black "Spassky"]\n[Result "*"]\n\n1. e4 *')
    class Response:
        def raise_for_status(self):pass
        def iter_bytes(self):yield stream.getvalue()
    @contextmanager
    def request(method,url,**kwargs):
        assert url=="https://www.pgnmentor.com/players/Fischer.zip"
        yield Response()
    monkeypatch.setattr("chessbuilder.champions.httpx.stream",request)
    assert download("Fischer")["added"]==1
    assert download("Fischer")["duplicates"]==1
    with pytest.raises(HTTPException) as e:download("../bad")
    assert e.value.status_code==404
