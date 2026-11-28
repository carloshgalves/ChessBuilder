"""Local historical collections from PGN Mentor."""
import argparse
import io
import json
import time
import zipfile
import httpx
from fastapi import APIRouter,HTTPException
from chessbuilder.db import connect,migrate
from chessbuilder.historical import Corpus,import_corpus
router=APIRouter(prefix="/api/champions")
SOURCE="https://www.pgnmentor.com/players/"
CHAMPIONS=(
("Steinitz","Wilhelm Steinitz","1886–1894"),
("Lasker","Emanuel Lasker","1894–1921"),
("Capablanca","José Raúl Capablanca","1921–1927"),
("Alekhine","Alexander Alekhine","1927–1935, 1937–1946"),
("Euwe","Max Euwe","1935–1937"),
("Botvinnik","Mikhail Botvinnik","1948–1963 (três períodos)"),
("Smyslov","Vasily Smyslov","1957–1958"),
("Tal","Mikhail Tal","1960–1961"),
("Petrosian","Tigran Petrosian","1963–1969"),
("Spassky","Boris Spassky","1969–1972"),
("Fischer","Bobby Fischer","1972–1975"),
("Karpov","Anatoly Karpov","1975–1985; FIDE 1993–1999"),
("Kasparov","Garry Kasparov","1985–2000"),
("Kramnik","Vladimir Kramnik","2000–2007"),
("Anand","Viswanathan Anand","FIDE 2000–2002; 2007–2013"),
("Carlsen","Magnus Carlsen","2013–2023"),
("Ding","Ding Liren","2023–2024"),
("Gukesh","Gukesh D","Título conquistado em 2024"),
("Khalifman","Alexander Khalifman","FIDE 1999–2000"),
("Ponomariov","Ruslan Ponomariov","FIDE 2002–2004"),
("Kasimdzhanov","Rustam Kasimdzhanov","FIDE 2004–2005"),
("Topalov","Veselin Topalov","FIDE 2005–2006"),
)
IDS={x[0] for x in CHAMPIONS}
MAX_BYTES=20_000_000
def unpack(data):
    if len(data)>MAX_BYTES:raise ValueError("ZIP excede limite")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        members=[m for m in z.infolist() if not m.is_dir() and m.filename.lower().endswith(".pgn")]
        if len(members)!=1 or members[0].file_size>MAX_BYTES:raise ValueError("Arquivo PGN inesperado ou muito grande")
        with z.open(members[0]) as f:raw=f.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError("PGN excede limite")
    try:return raw.decode("utf-8-sig")
    except UnicodeDecodeError:return raw.decode("latin-1")
@router.get("")
def catalog():
    with connect() as c:
        rows={r["edition"]:dict(r) for r in c.execute("""SELECT c.id,c.edition,COUNT(e.game_id) AS imported
            FROM historical_corpora c LEFT JOIN historical_entries e ON e.corpus_id=c.id
            WHERE c.edition LIKE 'Champion:%' AND c.provenance = ? || substr(c.edition,10) || '.zip'
            GROUP BY c.id""",(SOURCE,))}
    return [{"slug":slug,"name":name,"reign":reign,"category":"classical" if i<18 else "fide_split","source":SOURCE+slug+".zip",
        "corpus_id":rows.get("Champion:"+slug,{}).get("id"),"imported":rows.get("Champion:"+slug,{}).get("imported",0)} for i,(slug,name,reign) in enumerate(CHAMPIONS)]
@router.post("/{slug}/download")
def download(slug:str):
    if slug not in IDS:raise HTTPException(404,"Campeão fora do catálogo")
    url=SOURCE+slug+".zip"
    try:
        chunks=[];size=0
        with httpx.stream("GET",url,timeout=30,follow_redirects=False,headers={"User-Agent":"ChessBuilder/0.1"}) as r:
            r.raise_for_status()
            for chunk in r.iter_bytes():
                size+=len(chunk)
                if size>MAX_BYTES:raise ValueError("ZIP excede limite")
                chunks.append(chunk)
        pgn=unpack(b"".join(chunks))
        result=import_corpus(Corpus(edition="Champion:"+slug,provenance=url,pgn=pgn))
        return {"slug":slug,**result}
    except (httpx.HTTPError,ValueError,zipfile.BadZipFile,RuntimeError,NotImplementedError) as exc:
        raise HTTPException(502,"Importação indisponível: "+str(exc)[:300]) from exc
def cli():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("players",nargs="*")
    a=p.parse_args()
    if any(x not in IDS for x in a.players):p.error("Campeão fora do catálogo")
    migrate();failed=False
    for slug in a.players or [x[0] for x in CHAMPIONS]:
        try:
            r=download(slug)
            print(json.dumps({"slug":slug,"added":r["added"],"duplicates":r["duplicates"],"errors":len(r["errors"])}),flush=True)
            failed|=bool(r["errors"])
        except HTTPException as exc:
            print(json.dumps({"slug":slug,"error":exc.detail}),flush=True);failed=True
        time.sleep(.5)
    return int(failed)
if __name__=="__main__":raise SystemExit(cli())
