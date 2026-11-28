"""Review-first ingestion of privately held study materials."""
import base64
import hashlib
import io
import json
import re
from pathlib import Path
import chess
import chess.engine
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import FileResponse,PlainTextResponse
from pydantic import BaseModel,Field
from chessbuilder.db import connect,db_path
from chessbuilder.importer import parse
from chessbuilder.training import insert_item,digest,equivalent,ADJUDICATION_LOCK
from chessbuilder.engine import executable,serialize
from chessbuilder.tactics import legal_line

router=APIRouter(prefix="/api/books")
FEN_RE=re.compile(r"(?<!\S)(?:[prnbqkPRNBQK1-8]+/){7}[prnbqkPRNBQK1-8]+ [wb] (?:-|[KQkq]+) (?:-|[a-h][36]) \d+ \d+(?!\S)")
def folder():
    root=db_path().parent/"books";root.mkdir(mode=0o700,parents=True,exist_ok=True);return root

def material(c,mid):
    r=c.execute("SELECT * FROM book_materials WHERE id=?",(mid,)).fetchone()
    if not r:raise HTTPException(404,"Material inexistente")
    return r

def candidate(c,cid):
    r=c.execute("SELECT * FROM book_candidates WHERE id=?",(cid,)).fetchone()
    if not r:raise HTTPException(404,"Posição inexistente")
    return r

class Import(BaseModel):
    title:str=Field(min_length=1,max_length=200)
    provenance:str=Field(min_length=1,max_length=500)
    format:str=Field(pattern="^(pdf|pgn|fen)$")
    content:str=Field(min_length=1,max_length=40_000_000)

@router.post("/import")
def import_material(body:Import):
    pages=[];candidates=[]
    if body.format=="pdf":
        try:
            from pypdf import PdfReader
            raw=base64.b64decode(body.content,validate=True)
            if len(raw)>25_000_000:raise ValueError("PDF excede 25 MB")
            reader=PdfReader(io.BytesIO(raw))
            if reader.is_encrypted or len(reader.pages)>500:raise ValueError("PDF criptografado ou acima de 500 páginas")
            for i,page in enumerate(reader.pages,1):
                text=page.extract_text() or "";text=text[:50000];pages.append((i,text))
                for n,m in enumerate(FEN_RE.finditer(text),1):candidates.append((f"p{i}-fen{n}",i,m.group(0),[]))
            # PDF pages are retained for visual transcription and review.
            if not candidates:candidates.append(("revisar-diagramas",1,None,[]))
        except Exception as exc:raise HTTPException(422,"PDF não processável: "+str(exc)[:400]) from exc
    else:
        raw=body.content.encode("utf-8")
        if body.format=="fen":
            for i,line in enumerate(body.content.splitlines(),1):
                if line.strip():candidates.append((str(i),None,line.strip(),[]))
        else:
            for i,(g,_) in enumerate(parse(body.content),1):
                if g.errors:continue
                b=g.board()
                if not b.is_valid() or b.uci_variant!="chess" or b.chess960:continue
                pv=[m.uci() for m in g.mainline_moves()]
                if pv and len(pv)%2==0:pv=pv[:-1]
                candidates.append((str(i),None,b.fen(),[pv] if pv else []))
    if not candidates:raise HTTPException(422,"Nenhuma posição candidata")
    if len(candidates)>2000:raise HTTPException(422,"Limite de 2000 candidatos por material")
    sha=hashlib.sha256(raw).hexdigest()
    if body.format=="pdf":
        path=folder()/(sha+".pdf")
        if not path.exists():
            tmp=folder()/(sha+".tmp");tmp.write_bytes(raw);tmp.replace(path)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT OR IGNORE INTO book_materials(title,provenance,format,digest,pages) VALUES(?,?,?,?,?)",(body.title,body.provenance,body.format,sha,len(pages)))
        mid=c.execute("SELECT id FROM book_materials WHERE digest=?",(sha,)).fetchone()[0]
        for page,text in pages:c.execute("INSERT OR IGNORE INTO book_pages VALUES(?,?,?)",(mid,page,text))
        for label,page,fen,lines in candidates:c.execute("INSERT OR IGNORE INTO book_candidates(material_id,label,page,fen,author_lines) VALUES(?,?,?,?,?)",(mid,label,page,fen,json.dumps(lines)))
    return {"id":mid,"candidates":len(candidates),"pages":len(pages)}

@router.get("")
def listing():
    with connect() as c:return [dict(r) for r in c.execute("SELECT * FROM book_materials ORDER BY id DESC")]
@router.get("/{mid}/pdf")
def pdf(mid:int):
    with connect() as c:r=material(c,mid)
    path=folder()/(r["digest"]+".pdf")
    if r["format"]!="pdf" or not path.is_file():raise HTTPException(404,"PDF indisponível")
    return FileResponse(path,media_type="application/pdf",headers={"Content-Disposition":"inline","X-Content-Type-Options":"nosniff","Cache-Control":"no-store"})
@router.get("/{mid}/pages/{page}")
def page_text(mid:int,page:int):
    with connect() as c:
        r=c.execute("SELECT text FROM book_pages WHERE material_id=? AND page=?",(mid,page)).fetchone()
        if not r:raise HTTPException(404,"Página inexistente")
        return {"page":page,"text":r[0]}
@router.get("/{mid}/candidates")
def candidates(mid:int):
    with connect() as c:
        material(c,mid)
        return [{**dict(r),"author_lines":json.loads(r["author_lines"]),"engine_lines":json.loads(r["engine_lines"])} for r in c.execute("SELECT * FROM book_candidates WHERE material_id=? ORDER BY id",(mid,))]
class Draft(BaseModel):
    label:str=Field(min_length=1,max_length=120)
    page:int|None=Field(default=None,ge=1)
@router.post("/{mid}/candidates")
def add_candidate(mid:int,body:Draft):
    with connect() as c:
        m=material(c,mid)
        if body.page and m["format"]=="pdf" and body.page>m["pages"]:raise HTTPException(422,"Página inexistente")
        c.execute("INSERT OR IGNORE INTO book_candidates(material_id,label,page) VALUES(?,?,?)",(mid,body.label,body.page))
        return {"id":c.execute("SELECT id FROM book_candidates WHERE material_id=? AND label=?",(mid,body.label)).fetchone()[0]}

def parse_lines(board,raw):
    result=[]
    for line in raw:
        b=board.copy();pv=[]
        for token in line.split():
            try:m=b.parse_uci(token) if re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?",token) else b.parse_san(token)
            except ValueError:raise HTTPException(422,"Lance ilegal ou ambíguo: "+token)
            if m not in b.legal_moves:raise HTTPException(422,"Lance ilegal")
            pv.append(m.uci());b.push(m)
        if not pv or len(pv)%2==0:raise HTTPException(422,"Cada solução deve terminar em lance do jogador")
        result.append(pv)
    return result
def engine_lines(b):
    path=executable()
    if not path:raise HTTPException(409,"Stockfish não configurado")
    if not ADJUDICATION_LOCK.acquire(blocking=False):raise HTTPException(409,"Motor ocupado")
    e=None
    try:
        e=chess.engine.SimpleEngine.popen_uci(path,timeout=10);e.configure({"Threads":1,"Hash":32})
        scores=[serialize(x,b.turn) for x in e.analyse(b,chess.engine.Limit(nodes=180000,time=2),multipv=min(3,b.legal_moves.count()))]
        if not scores or (scores[0].get("depth") or 0)<10:raise HTTPException(409,"Busca insuficiente")
        return [s for s in scores if equivalent(scores[0],s) and s["pv"] and legal_line(b,s["pv"])]
    except (OSError,chess.engine.EngineError,TimeoutError) as exc:raise HTTPException(503,"Motor indisponível") from exc
    finally:
        try:
            if e:
                try:e.quit()
                except Exception:e.close()
        finally:ADJUDICATION_LOCK.release()

class Review(BaseModel):
    fen:str=Field(min_length=1,max_length=120)
    author_lines:list[str]=Field(default_factory=list,max_length=30)
    solution_page:int|None=Field(default=None,ge=1)
    mode:str=Field(pattern="^(author|engine|hybrid)$")
    themes:list[str]=Field(default_factory=list,max_length=12)
    note:str=Field(default="",max_length=3000)
    approved:bool=False

@router.post("/candidates/{cid}/review")
def review(cid:int,body:Review):
    try:b=chess.Board(body.fen)
    except ValueError:raise HTTPException(422,"FEN inválida")
    if not b.is_valid() or b.is_game_over(claim_draw=True):raise HTTPException(422,"Posição inválida ou encerrada")
    author=parse_lines(b,body.author_lines)
    if body.mode in ("author","hybrid") and not author:raise HTTPException(422,"Informe a solução do autor")
    motor=engine_lines(b) if body.mode in ("engine","hybrid") else []
    motor_pv=[x["pv"] for x in motor];motor_roots={pv[0] for pv in motor_pv}
    difference=bool(author and motor and any(pv[0] not in motor_roots for pv in author))
    if body.mode=="author":accepted=author
    else:accepted=[[pv[0]] for pv in motor_pv]
    if not accepted:raise HTTPException(409,"Nenhuma solução validada")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");r=candidate(c,cid);m=material(c,r["material_id"])
        if body.solution_page and m["format"]=="pdf" and body.solution_page>m["pages"]:raise HTTPException(422,"Página de solução inexistente")
        item=None
        if body.approved:
            evidence={"material_id":m["id"],"book_title":m["title"],"provenance":m["provenance"],"page":r["page"],"solution_page":body.solution_page,"author_lines":author,"engine_lines":motor_pv,"different":difference,"mode":body.mode,"note":body.note}
            k=digest(["book-v1",cid,b.fen(),accepted,evidence]);insert_item(c,"book",None,None,None,b,"book_"+body.mode,body.themes or ["book"],accepted,evidence,None,k)
            item=c.execute("SELECT id FROM training_items WHERE item_key=?",(k,)).fetchone()[0]
            c.execute("UPDATE training_items SET active=1 WHERE id=?",(item,))
            if r["item_id"] and r["item_id"]!=item:c.execute("UPDATE training_items SET active=0 WHERE id=?",(r["item_id"],))
        else:
            # Draft preview never creates a training exercise.
            if r["item_id"]:c.execute("UPDATE training_items SET active=0 WHERE id=?",(r["item_id"],))
        c.execute("UPDATE book_candidates SET fen=?,author_lines=?,engine_lines=?,solution_page=?,note=?,status=?,item_id=? WHERE id=?",(b.fen(),json.dumps(author),json.dumps(motor_pv),body.solution_page,body.note,"approved" if item else "draft",item,cid))
    return {"item_id":item,"author_lines":author,"engine_lines":motor_pv,"different":difference,"approved":bool(item)}
@router.get("/candidates/{cid}/fen",response_class=PlainTextResponse)
def export_fen(cid:int):
    with connect() as c:
        r=candidate(c,cid)
        if r["status"]!="approved":raise HTTPException(409,"Posição não revisada")
        return r["fen"]
@router.get("/candidates/{cid}/pgn",response_class=PlainTextResponse)
def export_pgn(cid:int):
    import chess.pgn
    with connect() as c:
        r=candidate(c,cid)
        if r["status"]!="approved":raise HTTPException(409,"Posição não revisada")
        g=chess.pgn.Game();g.setup(chess.Board(r["fen"]));g.headers["Event"]=r["label"]
        for pv in json.loads(r["author_lines"]) or json.loads(r["engine_lines"]):
            node=g
            for u in pv:node=node.add_variation(chess.Move.from_uci(u))
        return str(g)+"\n"
