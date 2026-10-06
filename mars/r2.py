"""Stockage objet Cloudflare R2 (compatible S3) : envoi vérifié, envoi en flux, liste, suppression, téléchargement.

Identifiants dans le .env du serveur : R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT, R2_BUCKET.
"""
import base64
import hashlib
import os
import re
from pathlib import Path


class R2:
    def __init__(self, client=None, bucket: str | None = None):
        """`client` et `bucket` permettent d'injecter un client S3 (tests) ; sinon, lus dans l'environnement."""
        if client is not None:
            self.s3, self.bucket = client, bucket
            return
        import boto3
        from botocore.config import Config

        missing = [k for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET")
                   if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"Identifiants R2 absents du .env : {', '.join(missing)}")
        self.bucket = os.environ["R2_BUCKET"]
        # Les sommes de contrôle automatiques des versions récentes de boto3 ne sont pas toutes acceptées par R2 :
        # on ne les calcule que si l'opération l'exige, et on fournit nous mêmes le MD5 des envois.
        # Un seau en juridiction européenne a une adresse en https://<compte>.eu.r2.cloudflarestorage.com.
        self.s3 = boto3.client(
            "s3", endpoint_url=os.environ["R2_ENDPOINT"], region_name="auto",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"},
                          request_checksum_calculation="when_required", response_checksum_validation="when_required"))

    def put_verified(self, data: bytes, key: str) -> dict:
        """Envoie un contenu en une seule requête avec son MD5 (R2 refuse un contenu altéré en route), puis vérifie
        l'objet stocké (verify). Lève une exception si l'objet ne correspond pas."""
        md5 = hashlib.md5(data)
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentMD5=base64.b64encode(md5.digest()).decode())
        method = self.verify(key, md5.hexdigest(), len(data))
        return {"bytes": len(data), "md5": md5.hexdigest(), "verification": method}

    def verify(self, key: str, md5_hex: str, size: int) -> str:
        """Confirme qu'un objet stocké est identique au contenu envoyé : même taille, et même MD5.

        L'ETag d'un objet envoyé en une requête est son MD5 : il suffit de le comparer. Celui d'un objet envoyé en
        plusieurs morceaux n'est pas le MD5 du fichier (il se termine par « -nombre de morceaux ») : l'objet est
        alors relu et son MD5 recalculé. Renvoie la méthode employée (etag ou relecture)."""
        head = self.s3.head_object(Bucket=self.bucket, Key=key)
        if head["ContentLength"] != size:
            raise RuntimeError(f"Envoi non confirmé pour {key} : {head['ContentLength']} octets sur R2 pour {size}")
        etag = head["ETag"].strip('"').lower()
        if re.fullmatch(r"[0-9a-f]{32}", etag):
            if etag != md5_hex:
                raise RuntimeError(f"Envoi non confirmé pour {key} : empreinte {etag} sur R2 contre {md5_hex}")
            return "etag"
        body, digest = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"], hashlib.md5()
        for block in iter(lambda: body.read(8 * 2**20), b""):
            digest.update(block)
        if digest.hexdigest() != md5_hex:
            raise RuntimeError(f"Envoi non confirmé pour {key} : relu {digest.hexdigest()} contre {md5_hex}")
        return "relecture"

    def put_stream(self, stream, key: str) -> dict:
        """Envoie un flux (sortie de pg_dump) en plusieurs parties, sans fichier local, en calculant au passage la
        taille et le MD5 de ce qui est lu. Mémoire bornée : deux parties de 16 Mo au plus en vol."""
        from boto3.s3.transfer import TransferConfig

        counter = _Counting(stream)
        self.s3.upload_fileobj(counter, self.bucket, key, Config=TransferConfig(
            multipart_threshold=16 * 2**20, multipart_chunksize=16 * 2**20, max_concurrency=2, use_threads=True))
        return {"bytes": counter.n, "md5": counter.md5.hexdigest()}

    def list(self, prefix: str) -> list[dict]:
        out = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            out += [{"key": o["Key"], "bytes": o["Size"], "modified": o["LastModified"]} for o in page.get("Contents", [])]
        return out

    def delete(self, key: str):
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def download(self, key: str, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.s3.download_file(self.bucket, key, str(path))


class _Counting:
    """Enveloppe de lecture qui compte les octets transmis et calcule leur MD5 (lecture séquentielle)."""

    def __init__(self, raw):
        self.raw, self.n, self.md5 = raw, 0, hashlib.md5()

    def read(self, size=-1):
        b = self.raw.read(size)
        self.n += len(b)
        self.md5.update(b)
        return b
