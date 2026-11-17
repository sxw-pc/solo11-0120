import hashlib
import json
from pathlib import Path
from .store import connect

def ingest(path, kind, file, handler):
    raw = Path(file).read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    sha = hashlib.sha256(raw).hexdigest()
    db = connect(path)
    try:
        with db:
            old = db.execute("SELECT result FROM source_blob WHERE kind=? AND sha=?", (kind,sha)).fetchone()
            if old: return json.loads(old["result"])
            source = db.execute("INSERT INTO source_blob(kind,sha,raw) VALUES (?,?,?)",(kind,sha,raw)).lastrowid
            result = handler(db,payload,source)
            db.execute("UPDATE source_blob SET result=? WHERE id=?",(json.dumps(result,ensure_ascii=False),source))
            return result
    except Exception as exc:
        with db:
            db.execute("INSERT INTO source_failure(kind,sha,raw,error) VALUES (?,?,?,?)",(kind,sha,raw,str(exc)))
        raise
    finally: db.close()
