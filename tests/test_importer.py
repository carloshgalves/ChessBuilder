import httpx
from chessbuilder.db import migrate,connect
from chessbuilder.importer import sync,parse,store
PGN='[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n[WhiteElo "1700"]\n[TimeControl "900+10"]\n\n1. e4 {[%clk 0:15:09]} e5 2. Nf3 Nc6 1-0\n'

def test_pgn_and_dedup(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    game,raw=next(parse(PGN))
    assert store(game,raw,username="alice")
    assert not store(game,raw,username="Alice")
    with connect() as c:
        g=c.execute("SELECT * FROM games").fetchone()
        assert g["player_color"]=="white" and g["speed"]=="rapid" and g["black_rating"] is None
        assert c.execute("SELECT clock FROM moves WHERE ply=1").fetchone()[0]==909

def test_chess_com_fixture(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    url="https://api.chess.com/pub/player/alice/games/2024/01"
    def handle(r):
        return httpx.Response(200,json={"archives":[url]} if str(r.url).endswith("archives") else {"games":[{"pgn":PGN,"url":"https://www.chess.com/game/live/1","time_class":"rapid","rules":"chess"}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert sync("chess.com","alice",client)["added"]==1
        assert sync("chess.com","alice",client)["added"]==0

def test_lichess_fixture(tmp_path,monkeypatch):
    import json
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    item={"pgn":PGN,"id":"abc","variant":"standard","createdAt":1700000000000,"speed":"rapid"}
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=json.dumps(item)+"\n"))) as client:
        assert sync("lichess","Alice",client)["added"]==1
        assert sync("lichess","Alice",client)["skipped"]==1

def test_lichess_retries_interrupted_stream(tmp_path,monkeypatch):
    import json
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    monkeypatch.setattr("chessbuilder.importer.time.sleep",lambda _:None)
    item={"pgn":PGN,"id":"abc","variant":"standard","createdAt":1700000000000,"speed":"rapid","opening":None}
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield (json.dumps(item)+"\n").encode()
            raise httpx.ReadError("connection interrupted")
    attempts=[]
    def handle(request):
        attempts.append(request)
        if len(attempts)==1:return httpx.Response(200,stream=Interrupted())
        return httpx.Response(200,text=json.dumps(item)+"\n")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result=sync("lichess","Alice",client)
    assert len(attempts)==2 and result["added"]==1 and result["skipped"]==1
    with connect() as c:
        assert c.execute("SELECT COUNT(*) FROM games").fetchone()[0]==1
        assert c.execute("SELECT cursor FROM accounts").fetchone()[0]=="1700000000000"

def test_reimport_assigns_missing_player_and_normalizes_opening(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse(PGN))
    assert store(g,p,meta={"opening":"https://www.chess.com/openings/Caro-Kann-Defense"})
    assert not store(g,p,username="Alice")
    with connect() as c:
        row=c.execute("SELECT player_color,opening FROM games").fetchone()
        assert tuple(row)==("white","Caro Kann Defense")

def test_chess_com_mixed_case_reuses_account_and_follows_redirect(tmp_path,monkeypatch):
    from chessbuilder.importer import account_id
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    original=account_id("chess.com","Alice")
    archive="https://api.chess.com/pub/player/alice/games/2024/01"
    requests=[]
    def handle(r):
        requests.append(str(r.url))
        assert "/Alice/" not in r.url.path
        if r.url.path.endswith("/archives"):
            return httpx.Response(301,headers={"Location":"/pub/player/alice/games/canonical-archives"})
        if r.url.path.endswith("/canonical-archives"):
            return httpx.Response(200,json={"archives":[archive]})
        return httpx.Response(200,json={"games":[{"pgn":PGN,"url":"https://www.chess.com/game/live/1","rules":"chess"}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert sync("chess.com","ALICE",client)["added"]==1
    assert len(requests)==3
    with connect() as c:
        assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]==1
        assert c.execute("SELECT account_id FROM games").fetchone()[0]==original["id"]

def test_chess_com_rejects_external_redirect(tmp_path,monkeypatch):
    import pytest
    from chessbuilder.importer import request
    calls=[]
    def handle(r):
        calls.append(str(r.url))
        return httpx.Response(301,headers={"Location":"https://example.org/archives"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError,match="Redirecionamento externo"):
            request(client,"https://api.chess.com/pub/player/alice/games/archives")
    assert len(calls)==1
