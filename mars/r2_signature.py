"""Signature S3 (AWS version 4), sans dépendance : adresse présignée (l'API lit les preuves images sur R2 sans embarquer
boto3 ; le conteneur taches, lui, écrit avec boto3, mars/r2.py) et en têtes signés (Copernicus Data Space, qui refuse
l'authentification par la requête, constaté le 14/10/2026). Vérifiées sur l'exemple de la documentation d'AWS et
contre botocore (tests/test_preuves.py, tests/test_cles_travailleur.py)."""
import hashlib
import hmac
import os
from datetime import datetime, timezone
from urllib.parse import quote, urlparse


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def presign(method: str, host: str, path: str, access_key: str, secret_key: str, region: str = "auto",
            expires: int = 300, now: datetime | None = None, scheme: str = "https") -> str:
    """Adresse présignée pour `method` sur `host` + `path` (chemin déjà débarrassé de son schéma), valable `expires`
    secondes à partir de `now`."""
    now = now or datetime.now(timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{day}/{region}/s3/aws4_request"
    params = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{access_key}/{scope}",
              "X-Amz-Date": amz_date, "X-Amz-Expires": str(expires), "X-Amz-SignedHeaders": "host"}
    query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items()))
    canonical = "\n".join([method, quote(path, safe="/-_.~"), query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), "s3"), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{scheme}://{host}{quote(path, safe='/-_.~')}?{query}&X-Amz-Signature={signature}"


EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def signed_headers(method: str, host: str, path: str, access_key: str, secret_key: str, region: str = "default",
                   now: datetime | None = None) -> dict:
    """En têtes d'une requête S3 signée par en tête (Authorization), corps vide. Copernicus Data Space n'accepte que
    cette forme : une adresse présignée (signature dans la requête) y est refusée en 403 InvalidAccessKeyId, même avec
    des clés valides. On peut y ajouter des en têtes non signés (Range)."""
    now = now or datetime.now(timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{day}/{region}/s3/aws4_request"
    names = "host;x-amz-content-sha256;x-amz-date"
    canonical = "\n".join([method, quote(path, safe="/-_.~"), "",
                           f"host:{host}\nx-amz-content-sha256:{EMPTY_SHA256}\nx-amz-date:{amz_date}\n", names,
                           EMPTY_SHA256])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), "s3"), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={names}, "
                             f"Signature={signature}",
            "x-amz-date": amz_date, "x-amz-content-sha256": EMPTY_SHA256}


def r2_get_url(key: str, expires: int = 300) -> str | None:
    """Adresse de lecture présignée d'un objet du seau R2 (identifiants du .env), ou None s'ils manquent."""
    env = {k: os.environ.get(k) for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET")}
    if not all(env.values()):
        return None
    u = urlparse(env["R2_ENDPOINT"])
    return presign("GET", u.netloc, f"/{env['R2_BUCKET']}/{key}", env["R2_ACCESS_KEY_ID"], env["R2_SECRET_ACCESS_KEY"],
                   expires=expires, scheme=u.scheme or "https")
