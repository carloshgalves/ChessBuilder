"""Local historical corpus inventory and curated exercises."""
import json
import chess
import httpx
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field
from chessbuilder.db import connect
from chessbuilder.importer import parse,store
from chessbuilder.training import insert_item,digest,verified_solutions
from chessbuilder.review import select_job,get_analyses

router=APIRouter(prefix="/api/historical")
EDITION_IDS=tuple(["WorldChamp"+y for y in "2024 2023 2021 2018 2016 2014 2013 2012 2010 2008 2007 2006 2004 2000 1990 1987 1986 1985 1984 1981 1978 1972 1969 1966 1963 1961 1960 1958 1957 1954 1951 1948 1937 1935 1934 1929 1927 1921 1910a 1910b 1909 1908 1907 1896 1894 1892 1890 1889 1886".split()]+["FideChamp"+y for y in "2005 2004 2002 2000 1999 1998 1996 1993".split()]+["PCAChamp1995","PCAChamp1993"])
class Corpus(BaseModel):
    edition:str=Field(min_length=1,max_length=120)
    provenance:str=Field(min_length=1,max_length=500)
    pgn:str=Field(min_length=1,max_length=20_000_000)
    expected_games:int|None=Field(default=None,ge=1,le=10000)
@router.post("/import")
def import_corpus(body:Corpus):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO historical_corpora(edition,provenance,expected_games) VALUES(?,?,?)",(body.edition,body.provenance,body.expected_games))
        cid=c.execute("SELECT id FROM historical_corpora WHERE edition=? AND provenance=?",(body.edition,body.provenance)).fetchone()[0]
        if body.expected_games is not None:c.execute("UPDATE historical_corpora SET expected_games=? WHERE id=?",(body.expected_games,cid))
    added=duplicates=0;errors=[]
    for i,(game,raw) in enumerate(parse(body.pgn),1):
        try:
            h=game.headers;identity=digest([game.board().fen(),h.get("White","").casefold(),h.get("Black","").casefold(),h.get("Result"),[m.uci() for m in game.mainline_moves()]])
            store(game,raw,"historical",identity)
            with connect() as c:
                gid=c.execute("SELECT id FROM games WHERE source='historical' AND external_id=?",(identity,)).fetchone()[0]
                c.execute("INSERT OR IGNORE INTO historical_entries VALUES(?,?,?,?)",(cid,gid,h.get("Event"),h.get("Round")))
                linked=c.execute("SELECT changes()").fetchone()[0]
            added+=linked;duplicates+=1-linked
        except (ValueError,TypeError,KeyError) as exc:errors.append({"game":i,"error":str(exc)})
    return {"corpus_id":cid,"added":added,"duplicates":duplicates,"errors":errors}
@router.post("/download/{edition}")
def download(edition:str):
    if edition not in EDITION_IDS:raise HTTPException(404,"Edição fora do catálogo")
    url="https://www.pgnmentor.com/events/"+edition+".pgn"
    try:
        chunks=[];size=0
        with httpx.stream("GET",url,timeout=20,follow_redirects=False,headers={"User-Agent":"ChessBuilder/0.1"}) as r:
            r.raise_for_status()
            for chunk in r.iter_bytes():
                size+=len(chunk)
                if size>20_000_000:raise ValueError("Arquivo excede limite")
                chunks.append(chunk)
        raw=b"".join(chunks)
        try:pgn=raw.decode("utf-8-sig")
        except UnicodeDecodeError:pgn=raw.decode("latin-1")
        return import_corpus(Corpus(edition=edition,provenance=url,pgn=pgn))
    except (httpx.HTTPError,ValueError) as exc:raise HTTPException(502,"Não foi possível importar a edição: "+str(exc)) from exc
@router.get("/catalog")
def catalog():
    with connect() as c:
        rows=[dict(r) for r in c.execute("""SELECT c.*,COUNT(e.game_id) AS imported FROM historical_corpora c LEFT JOIN historical_entries e ON e.corpus_id=c.id GROUP BY c.id ORDER BY c.edition""")]
    for r in rows:r["missing"]=max(0,r["expected_games"]-r["imported"]) if r["expected_games"] is not None else None
    return {"source":"https://www.pgnmentor.com/files.html","editions":[{"edition":e,"imported":any(r["edition"]==e and r["imported"]>0 for r in rows)} for e in EDITION_IDS],"corpora":rows}
@router.get("/games")
def games(corpus_id:int|None=None,player:str="",limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0)):
    with connect() as c:return [dict(r) for r in c.execute("""SELECT DISTINCT g.id,g.white,g.black,g.played_at,g.result,(SELECT COUNT(*) FROM moves m WHERE m.game_id=g.id) AS plies FROM historical_entries e JOIN games g ON g.id=e.game_id WHERE (? IS NULL OR e.corpus_id=?) AND (g.white LIKE ? OR g.black LIKE ?) ORDER BY g.played_at DESC,g.id LIMIT ? OFFSET ?""",(corpus_id,corpus_id,"%"+player+"%","%"+player+"%",limit,offset))]
class Curate(BaseModel):
    game_id:int
    ply:int=Field(ge=1)
    mode:str=Field(default="master",pattern="^(master|engine|line)$")
    themes:list[str]=Field(default_factory=list,max_length=12)
    difficulty:int=Field(default=3,ge=1,le=5)
    note:str=Field(default="",max_length=2000)
@router.post("/curate")
def curate(body:Curate):
    with connect() as c:
        g=c.execute("SELECT * FROM games WHERE id=? AND source='historical'",(body.game_id,)).fetchone()
        if not g:raise HTTPException(404,"Partida histórica inexistente")
        moves=c.execute("SELECT ply,uci FROM moves WHERE game_id=? ORDER BY ply",(body.game_id,)).fetchall()
        if body.ply>len(moves):raise HTTPException(422,"Lance fora da partida")
        b=chess.Board(g["initial_fen"])
        for m in moves[:body.ply-1]:b.push_uci(m["uci"])
        historical=moves[body.ply-1]["uci"];job=select_job(c,body.game_id);a=get_analyses(c,job).get(body.ply)
        if body.mode=="master":solutions=[[historical]]
        else:
            solutions=verified_solutions(b,a) if a else []
            if not solutions:raise HTTPException(409,"Analise a partida em nível profundo antes de curar solução do motor.")
            if body.mode=="engine":solutions=[pv[:1] for pv in solutions]
            else:solutions=[pv[:min(5,len(pv) if len(pv)%2 else len(pv)-1)] for pv in solutions]
        evidence={"historical_move":historical,"historical_san":b.san(chess.Move.from_uci(historical)),"engine_best":a.get("best") if a else None,"mode":body.mode,"note":body.note,"source_game":body.game_id}
        item_key=digest(["historical-v1",body.model_dump(),solutions])
        insert_item(c,"historical",body.game_id,body.ply,job["id"] if job else None,b,"historical_"+body.mode,body.themes or ["historical"],solutions,evidence,body.difficulty,item_key)
        return {"item_id":c.execute("SELECT id FROM training_items WHERE item_key=?",(item_key,)).fetchone()[0],"fen":b.fen()}
@router.get("/positions")
def positions(theme:str|None=None,player:str="",difficulty:int|None=None):
    with connect() as c:return [{"id":r["id"],"game_id":r["game_id"],"ply":r["ply"],"themes":json.loads(r["themes"]),"kind":r["kind"],"difficulty":r["difficulty"],"white":r["white"],"black":r["black"]} for r in c.execute("""SELECT t.*,g.white,g.black FROM training_items t JOIN games g ON g.id=t.game_id WHERE t.source='historical' AND t.active=1 AND (? IS NULL OR EXISTS(SELECT 1 FROM json_each(t.themes) WHERE value=?)) AND (? IS NULL OR t.difficulty=?) AND (g.white LIKE ? OR g.black LIKE ?) ORDER BY t.id DESC LIMIT 200""",(theme,theme,difficulty,difficulty,"%"+player+"%","%"+player+"%"))]
