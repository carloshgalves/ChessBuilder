import hashlib,json
from pathlib import Path
def test_local_artwork_integrity():
    root=Path(__file__).parents[1]/"frontend/public/piece-assets"
    manifest=json.loads((root/"SOURCES.json").read_text())
    assert len(manifest["sets"])==7
    for group in manifest["sets"]:
        assert len(group["files"])==12
        for name,entry in group["files"].items():
            data=(root/group["id"]/name).read_bytes()
            assert len(data)==entry["size"]
            assert hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()==entry["sha"]
