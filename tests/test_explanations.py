import pytest
from chessbuilder.db import migrate,connect
from chessbuilder.explanations import assist,Assist,validate
from chessbuilder.training import digest

def test_external_disabled_and_cache(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    payload={"version":"fixture","fen":"fixture","played_uci":"e2e4","facts":[{"id":1,"kind":"evaluation","text":"test","evidence":{}}]}
    monkeypatch.setattr("chessbuilder.explanations.local_payload",lambda *a:payload)
    calls=[]
    monkeypatch.setattr("chessbuilder.explanations.provider",lambda cfg,p:(calls.append(p) or {"steps":[{"id":1,"mode":"observe"}]}))
    req=Assist(game_id=1,ply=1,payload_digest=digest(payload),fact_ids=[1],consent=True)
    monkeypatch.delenv("CHESSBUILDER_EXPLAIN_ENABLED",raising=False)
    assert assist(req)["source"]=="local" and not calls
    monkeypatch.setenv("CHESSBUILDER_EXPLAIN_ENABLED","1");monkeypatch.setenv("CHESSBUILDER_EXPLAIN_API_KEY","fixture");monkeypatch.setenv("CHESSBUILDER_EXPLAIN_MODEL","fixture")
    assert assist(req.model_copy(update={"consent":False}))["source"]=="local" and not calls
    assert assist(req)["source"]=="external" and len(calls)==1
    assert assist(req)["source"]=="cache" and len(calls)==1
    with pytest.raises(ValueError):validate({"steps":[{"id":2,"mode":"observe"}]},{1})
    with pytest.raises(ValueError):validate({"steps":[{"id":1,"mode":"observe","text":"invented"}]},{1})
