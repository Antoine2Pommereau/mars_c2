"""Vérification des envois sur R2 : envoi simple (ETag égal au MD5), envoi en plusieurs morceaux (ETag sans MD5 du
fichier, relecture), contenu altéré ou tronqué refusé. Faux client S3 en mémoire."""
import hashlib
import io

import pytest

from mars.r2 import R2


class FakeS3:
    def __init__(self, corrupt=False, truncate=False):
        self.objects, self.etags, self.corrupt, self.truncate = {}, {}, corrupt, truncate

    def _store(self, key, data, etag=None):
        """Contenu reçu altéré ou tronqué en route ; l'ETag d'un envoi simple est le MD5 de ce qui est stocké."""
        if self.corrupt:
            data = b"X" + data[1:]
        if self.truncate:
            data = data[:-1]
        self.objects[key], self.etags[key] = data, etag or hashlib.md5(data).hexdigest()

    def put_object(self, **kw):
        assert kw["Bucket"] == "seau" and kw["ContentMD5"]
        self._store(kw["Key"], kw["Body"])

    def upload_fileobj(self, f, bucket, key, **kw):
        assert bucket == "seau" and "Config" in kw
        data = b"".join(iter(lambda: f.read(5), b""))
        self._store(key, data, "0123456789abcdef0123456789abcdef-3")     # forme d'un ETag d'envoi en morceaux

    def head_object(self, **kw):
        assert kw["Bucket"] == "seau"
        return {"ContentLength": len(self.objects[kw["Key"]]), "ETag": f'"{self.etags[kw["Key"]]}"'}

    def get_object(self, **kw):
        assert kw["Bucket"] == "seau"
        return {"Body": io.BytesIO(self.objects[kw["Key"]])}


DATA = b"positions compactees " * 1000


def test_single_put_confirmed_by_etag():
    r2 = R2(FakeS3(), "seau")
    assert r2.put_verified(DATA, "a")["verification"] == "etag"


def test_multipart_confirmed_by_rereading():
    r2 = R2(FakeS3(), "seau")
    sent = r2.put_stream(io.BytesIO(DATA), "b")
    assert sent == {"bytes": len(DATA), "md5": hashlib.md5(DATA).hexdigest()}
    assert r2.verify("b", sent["md5"], sent["bytes"]) == "relecture"


def test_corrupted_multipart_is_refused():
    r2 = R2(FakeS3(corrupt=True), "seau")
    sent = r2.put_stream(io.BytesIO(DATA), "c")
    with pytest.raises(RuntimeError, match="relu"):
        r2.verify("c", sent["md5"], sent["bytes"])


def test_truncated_or_altered_single_put_is_refused():
    with pytest.raises(RuntimeError, match="octets"):
        R2(FakeS3(truncate=True), "seau").put_verified(DATA, "d")
    with pytest.raises(RuntimeError, match="empreinte"):
        R2(FakeS3(corrupt=True), "seau").put_verified(DATA, "e")
