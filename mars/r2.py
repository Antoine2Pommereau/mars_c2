"""Stockage objet Cloudflare R2 (compatible S3) : envoi vérifié, envoi en flux, liste, suppression, téléchargement.

Identifiants dans le .env du serveur : R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT, R2_BUCKET.
"""
import base64
import hashlib
import os
from pathlib import Path


class R2:
    def __init__(self):
        import boto3
        from botocore.config import Config

        missing = [k for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET")
                   if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"Identifiants R2 absents du .env : {', '.join(missing)}")
        self.bucket = os.environ["R2_BUCKET"]
        # Les sommes de contrôle automatiques des versions récentes de boto3 ne sont pas toutes acceptées par R2 :
        # on ne les calcule que si l'opération l'exige, et on fournit nous mêmes le MD5 des envois.
        self.s3 = boto3.client(
            "s3", endpoint_url=os.environ["R2_ENDPOINT"], region_name="auto",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"},
                          request_checksum_calculation="when_required", response_checksum_validation="when_required"))

    def put_verified(self, data: bytes, key: str) -> dict:
        """Envoie un contenu en une seule requête avec son MD5 (R2 refuse un contenu altéré en route), puis relit
        l'objet : taille et empreinte doivent correspondre. Lève une exception sinon."""
        md5 = hashlib.md5(data)
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentMD5=base64.b64encode(md5.digest()).decode())
        head = self.s3.head_object(Bucket=self.bucket, Key=key)
        etag = head["ETag"].strip('"')
        if head["ContentLength"] != len(data) or etag != md5.hexdigest():
            raise RuntimeError(f"Envoi non confirmé pour {key} : {head['ContentLength']} octets sur R2 pour "
                               f"{len(data)} en local, empreinte {etag} contre {md5.hexdigest()}")
        return {"bytes": len(data), "md5": md5.hexdigest()}

    def put_stream(self, stream, key: str) -> int:
        """Envoie un flux (sortie de pg_dump) en plusieurs parties, sans fichier local, et renvoie le nombre
        d'octets lus. Mémoire bornée : deux parties de 16 Mo au plus en vol."""
        from boto3.s3.transfer import TransferConfig

        counter = _Counting(stream)
        self.s3.upload_fileobj(counter, self.bucket, key, Config=TransferConfig(
            multipart_threshold=16 * 2**20, multipart_chunksize=16 * 2**20, max_concurrency=2, use_threads=True))
        return counter.n

    def size(self, key: str) -> int:
        return self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"]

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
    """Enveloppe de lecture qui compte les octets transmis."""

    def __init__(self, raw):
        self.raw, self.n = raw, 0

    def read(self, size=-1):
        b = self.raw.read(size)
        self.n += len(b)
        return b
